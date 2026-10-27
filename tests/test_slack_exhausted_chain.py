"""M1-95: Slack commands without an id find an open incident whose chain is exhausted.

In the 2026-10-24 live round ``/ack`` without an id answered "no active page
found" after a one-level Escalation Chain was exhausted, though the incident
was open and held. The lookup now also takes the user's latest page on an open
incident whose chain is exhausted, when that page was sent in the last 24
hours. A running, snoozed or held chain qualifies at any age, as before; a
chain acknowledged before the lock existed (finished) does not. A resolved
or merged incident never qualifies. The newest qualifying page wins.
"""

# The app and client fixtures come from the Slack paging tests.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.db.models import IncidentPage
from backend.db.repos import (
    EscalationChainRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    TeamRepo,
)
from tests.test_slack_paging import (  # noqa: F401
    TEST_ORG_ID,
    _seed_incident,
    _seed_slack_connector,
    _seed_user_and_link,
    _slack_sign,
    _slash_body,
    app,
    client,
)

NO_PAGE = "no active page found"
HOUR = 60
DAY = 24 * HOUR


async def _paged(
    app,
    user,
    *,
    title,
    chain_status,
    incident_status="open",
    age=0,
    finished=False,
):
    """An incident paged to ``user`` ``age`` minutes ago, its chain in
    ``chain_status`` and the incident in ``incident_status``. The page's
    ``sent_at`` is what the 24 hour window for an exhausted chain checks.
    An exhausted or cancelled chain gets a ``finished_at``, as the engine
    writes it; ``finished`` sets one for any other status, as an
    acknowledgement made before the lock existed left it."""
    incident = await _seed_incident(app, title=title)
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name=title, slug=f"t-{uuid.uuid4().hex[:8]}"
        )
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name=title, description=None
        )
        state = await IncidentChainStateRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain.id
        )
        state.status = chain_status
        if finished or chain_status in ("exhausted", "cancelled"):
            state.finished_at = datetime.now(timezone.utc) - timedelta(minutes=age)
        page = await IncidentPageRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=user.id
        )
        page.sent_at = datetime.now(timezone.utc) - timedelta(minutes=age)
        row = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
        row.status = incident_status
        await db.commit()
    return incident


async def _page_count(app, incident_id):
    async with app.state.session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(IncidentPage)
            .where(IncidentPage.incident_id == incident_id)
        )


async def _bare(client, slack_user_id, command="/ack"):
    """Run ``command`` with no incident id and return the reply text."""
    body = _slash_body(command=command, text="", user_id=slack_user_id)
    resp = await client.post(
        "/bot/slack/commands", content=body, headers=_slack_sign(body)
    )
    assert resp.status_code == 200
    return resp.json()["text"]


async def test_ack_finds_an_open_incident_whose_chain_is_exhausted(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_EX"
    )
    incident = await _paged(
        app, user, title="exhausted-but-open", chain_status="exhausted"
    )

    # The chain ran out, so there is no lock to hold: the user is recorded
    # as the owner.
    assert await _bare(client, "U_EX") == "You recorded *exhausted-but-open*."
    async with app.state.session_factory() as db:
        active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident.id)
        assert active is not None and active.assigned_to == user.id


async def test_ack_never_takes_a_resolved_or_merged_incident(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_RS"
    )
    await _paged(
        app,
        user,
        title="resolved",
        chain_status="exhausted",
        incident_status="resolved",
    )
    await _paged(
        app, user, title="merged", chain_status="exhausted", incident_status="merged"
    )

    assert NO_PAGE in await _bare(client, "U_RS")


async def test_a_newer_page_on_a_resolved_incident_does_not_hide_an_open_one(
    client, app
):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_NW"
    )
    await _paged(app, user, title="older-open", chain_status="exhausted", age=30)
    await _paged(
        app,
        user,
        title="newer-resolved",
        chain_status="exhausted",
        incident_status="resolved",
        age=1,
    )

    assert "older-open" in await _bare(client, "U_NW")


async def test_a_running_chain_is_still_found_and_a_cancelled_one_is_not(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_RN"
    )
    await _paged(app, user, title="cancelled-chain", chain_status="cancelled", age=1)
    assert NO_PAGE in await _bare(client, "U_RN")

    await _paged(app, user, title="running-chain", chain_status="running", age=5)
    assert "running-chain" in await _bare(client, "U_RN")


async def test_an_exhausted_chain_paged_more_than_a_day_ago_is_not_found(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_OLD"
    )
    await _paged(
        app, user, title="ran-out-yesterday", chain_status="exhausted", age=25 * HOUR
    )
    assert NO_PAGE in await _bare(client, "U_OLD")

    await _paged(
        app, user, title="ran-out-today", chain_status="exhausted", age=23 * HOUR
    )
    assert "ran-out-today" in await _bare(client, "U_OLD")


async def test_a_bare_resolve_leaves_an_old_exhausted_incident_alone(client, app):
    """The review case: the newest page is on an incident a teammate resolved,
    and an exhausted chain from weeks ago is still open. A bare ``/resolve``
    must not fall through to that old incident."""
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_ST"
    )
    stale = await _paged(
        app, user, title="stale-weeks-old", chain_status="exhausted", age=21 * DAY
    )
    await _paged(
        app,
        user,
        title="current-resolved",
        chain_status="cancelled",
        incident_status="resolved",
        age=2,
    )

    text = await _bare(client, "U_ST", command="/resolve")
    assert NO_PAGE in text
    async with app.state.session_factory() as db:
        reloaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, stale.id)
        assert reloaded.status == "open"


@pytest.mark.parametrize("chain_status", ["running", "paused", "acked"])
async def test_a_live_chain_is_found_however_old_its_page(client, app, chain_status):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id=f"U_LV_{chain_status}"
    )
    await _paged(
        app, user, title=f"live-{chain_status}", chain_status=chain_status, age=3 * DAY
    )

    text = await _bare(client, f"U_LV_{chain_status}", command="/opsmender-status")
    assert f"*live-{chain_status}*" in text
    assert f"chain `{chain_status}`" in text


async def test_a_held_chain_on_a_resolved_incident_is_not_found(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_HR"
    )
    await _paged(
        app,
        user,
        title="held-then-resolved",
        chain_status="acked",
        incident_status="resolved",
        age=5,
    )

    assert NO_PAGE in await _bare(client, "U_HR")


async def test_a_chain_acknowledged_before_the_lock_existed_is_not_found(client, app):
    """A chain acknowledged before the acknowledgement lock kept its
    ``finished_at`` and can no longer page, so it is not live at any age:
    a bare command must not act on that weeks-old incident."""
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_LG"
    )
    await _paged(
        app,
        user,
        title="legacy-acked",
        chain_status="acked",
        age=30 * DAY,
        finished=True,
    )

    assert NO_PAGE in await _bare(client, "U_LG", command="/resolve")


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (("exhausted", 10), ("running", 2 * HOUR), "exhausted"),
        (("exhausted", 2 * HOUR), ("paused", 5), "paused"),
    ],
    ids=["newer-exhausted", "newer-paused"],
)
async def test_the_newest_qualifying_page_wins(client, app, first, second, expected):
    """Two pages qualify; the newer one is picked, whichever was written
    first."""
    connector = await _seed_slack_connector(app)
    slack_id = f"U_NQ_{expected}"
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id=slack_id
    )
    for chain_status, age in (first, second):
        await _paged(
            app, user, title=f"page-{chain_status}", chain_status=chain_status, age=age
        )

    text = await _bare(client, slack_id, command="/opsmender-status")
    assert f"*page-{expected}*" in text


async def test_another_users_exhausted_page_is_not_picked(client, app):
    connector = await _seed_slack_connector(app)
    paged_user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_PG"
    )
    await _seed_user_and_link(app, connector_id=connector.id, slack_user_id="U_OT")
    await _paged(app, paged_user, title="not-yours", chain_status="exhausted")

    assert NO_PAGE in await _bare(client, "U_OT")


async def test_every_command_without_an_id_acts_on_a_chain_that_ran_out(client, app):
    """Each command picks the incident; what it then does is its own rule.
    A chain that ran out has nothing left to snooze, a release pages nobody,
    and once resolved the incident is no longer picked."""
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_EV"
    )
    incident = await _paged(
        app, user, title="ran-out", chain_status="exhausted", age=30
    )

    assert await _bare(client, "U_EV", command="/snooze-escalation") == (
        "No active chain to snooze for *ran-out*."
    )
    assert await _bare(client, "U_EV", command="/take") == (
        "You're now assigned to *ran-out*."
    )
    pages = await _page_count(app, incident.id)
    assert await _bare(client, "U_EV", command="/release") == "Released *ran-out*."
    assert await _page_count(app, incident.id) == pages
    async with app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.status == "exhausted"
    assert await _bare(client, "U_EV", command="/resolve") == (
        "Marked *ran-out* resolved."
    )
    async with app.state.session_factory() as db:
        reloaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
        assert reloaded.status == "resolved"
    assert NO_PAGE in await _bare(client, "U_EV")


async def test_status_without_an_id_reports_a_chain_that_ran_out_today(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_SS"
    )
    await _paged(
        app, user, title="ran-out-last-week", chain_status="exhausted", age=7 * DAY
    )
    # No page qualifies, so the org overview lists running and snoozed chains.
    assert (
        await _bare(client, "U_SS", command="/opsmender-status")
        == "No active escalation chains. :tada:"
    )

    await _paged(
        app, user, title="ran-out-an-hour-ago", chain_status="exhausted", age=HOUR
    )
    text = await _bare(client, "U_SS", command="/opsmender-status")
    assert "*ran-out-an-hour-ago*" in text
    assert "chain `exhausted`" in text
