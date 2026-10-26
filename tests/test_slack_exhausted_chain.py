"""M1-95: Slack commands without an id find an open incident whose chain is exhausted.

In the 2026-10-24 live round ``/ack`` without an id answered "no active page
found" after a one-level Escalation Chain was exhausted, though the incident
was open and held. The lookup now also takes the user's latest page on an open
incident whose chain is exhausted; a resolved or merged incident never
qualifies.
"""

# The app and client fixtures come from the Slack paging tests.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from backend.db.repos import (
    EscalationChainRepo,
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


async def _paged(app, user, *, title, chain_status, incident_status="open", age=0):
    """An incident paged to ``user`` ``age`` minutes ago, its chain in
    ``chain_status`` and the incident in ``incident_status``."""
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
        page = await IncidentPageRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=user.id
        )
        page.sent_at = datetime.now(timezone.utc) - timedelta(minutes=age)
        row = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
        row.status = incident_status
        await db.commit()
    return incident


async def _ack(client, slack_user_id):
    body = _slash_body(command="/ack", text="", user_id=slack_user_id)
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
    await _paged(app, user, title="exhausted-but-open", chain_status="exhausted")

    text = await _ack(client, "U_EX")
    assert "exhausted-but-open" in text
    assert NO_PAGE not in text


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

    assert NO_PAGE in await _ack(client, "U_RS")


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

    assert "older-open" in await _ack(client, "U_NW")


async def test_a_running_chain_is_still_found_and_a_cancelled_one_is_not(client, app):
    connector = await _seed_slack_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_RN"
    )
    await _paged(app, user, title="cancelled-chain", chain_status="cancelled", age=1)
    assert NO_PAGE in await _ack(client, "U_RN")

    await _paged(app, user, title="running-chain", chain_status="running", age=5)
    assert "running-chain" in await _ack(client, "U_RN")
