"""M1-100: incident cards to a Microsoft Teams (Workflows) channel.

Each incident change posts one card to the channel's fixed destination, with
signed links on the Respond lane while the incident is open. At most 20 posts
in 5 minutes go to one channel, counted from committed audit rows; past that,
or while the workflow is not answering, a card is held without marking the
channel failed, and the sweep posts the newest state of each held incident
when there is room (open and higher priority first, each once). A transient
failure pauses the channel with a growing wait; a refused post marks it failed
and stops the sweep until a post goes through. AI session events, ownership
refreshes and My Routing stages post nothing. SQLite; the workflow is an
``httpx.MockTransport``, so nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.bot_connectors import _validate_platform_lanes
from backend.api.routes.incidents import _SKIP_REASONS, get_incident_timeline
from backend.bots import notifier
from backend.bots.connectors import registry
from backend.bots.connectors.teams_workflows import TeamsWorkflowsAdapter
from backend.bots.incident_card import SIGN_IN_TO_ACT_HINT
from backend.bots.post_budget import (
    COUNTED_STATUSES,
    RETRY_PAUSES_SECONDS,
    _as_utc,
    budget_for,
    channel_state,
)
from backend.db.models import Base, BotActionAudit, BotConnector, Organization
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentNotificationReceiptRepo,
    IncidentRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import notification_escalation as ne
from backend.paging.routing import CHANNEL_POSTS_ONLY_SKIP_REASON
from backend.paging.scheduler import EscalationScheduler
from backend.paging.teams_cards import EXHAUSTED_NOTE, LINKS_NOTE

ORG = uuid.UUID("00000000-0000-0000-0000-000000000110")
PUBLIC = "https://ops.example.com"
URL = (
    "https://prod-12.westus.logic.azure.com/workflows/abc/triggers/manual/paths/"
    "invoke?api-version=2016-06-01&sig=s"
)
CONFIG = SimpleNamespace(
    auth=SimpleNamespace(jwt_secret="test-secret", action_link_ttl_minutes=1440)
)
ALL_BUTTONS = [
    "Acknowledge",
    "Resolve",
    "Escalate",
    "Start AI session",
    "View in OpsMender",
]
VIEW_ONLY = ["View in OpsMender"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workflows-delivery.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(Organization(id=ORG, name="Acme", slug="acme"))
        await db.commit()
    yield session_factory
    await engine.dispose()


class Workflow:
    """A mocked workflow trigger: keeps every posted card and answers with the
    next queued status (202 by default) or raises the queued exception."""

    def __init__(self):
        self.posted: list[dict] = []
        self.answers: list = []
        self.order: list[str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.posted.append(json.loads(request.content)["attachments"][0]["content"])
        if self.order is not None:
            self.order.append("workflow")
        answer = self.answers.pop(0) if self.answers else 202
        if isinstance(answer, int):
            return httpx.Response(answer)
        raise answer("down", request=request)


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.setenv("OPSMENDER_PUBLIC_URL", PUBLIC)
    mock = Workflow()
    adapter = TeamsWorkflowsAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(mock.handler)
        ),
        config_loader=lambda: CONFIG,
    )
    monkeypatch.setitem(registry._ADAPTERS, "teams_workflows", adapter)
    return mock


# -- Helpers ---------------------------------------------------------------------


async def _channel(factory, **overrides) -> BotConnector:
    values = dict(
        name="a-teams-cards",
        platform="teams_workflows",
        config={},
        credentials={"webhook_url": URL},
        allowed_capabilities=["notifications"],
        status="healthy",
        is_enabled=True,
    )
    values.update(overrides)
    async with factory() as db:
        connector = await BotConnectorRepo.create(db, ORG, **values)
        await db.commit()
    return connector


async def _telegram(factory) -> BotConnector:
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            ORG,
            name="z-telegram",
            platform="telegram",
            config={"allowed_chat_ids": ["-100777"]},
            credentials={"bot_token": "BOT-TOKEN"},
            allowed_capabilities=["notifications"],
            status="healthy",
            is_enabled=True,
        )
        await db.commit()
    return connector


async def _team(factory, name: str):
    async with factory() as db:
        team = await TeamRepo.create(db, ORG, name=name, slug=name.lower())
        await db.commit()
    return team


async def _incident(factory, *, title="Checkout 500s", priority="P1", **values):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db,
            ORG,
            title=title,
            description="Spike in 500s",
            severity="critical",
            priority=priority,
        )
        for key, value in values.items():
            setattr(incident, key, value)
        await db.commit()
    return incident


async def _set(factory, incident_id, **values) -> None:
    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, ORG, incident_id)
        for key, value in values.items():
            setattr(incident, key, value)
        await db.commit()


async def _event(factory, incident_id, event_type="incident.created", **kwargs):
    return await notifier.deliver_incident_event(
        factory, org_id=ORG, incident_id=incident_id, event_type=event_type, **kwargs
    )


async def _seed(
    factory,
    connector,
    count: int,
    *,
    status="ok",
    command="notify:incident.created",
    ago=timedelta(seconds=30),
) -> None:
    """Committed post rows, as other processes would have written them."""
    async with factory() as db:
        for _ in range(count):
            db.add(
                BotActionAudit(
                    org_id=ORG,
                    connector_id=connector.id,
                    platform=connector.platform,
                    chat_id="workflow",
                    command=command,
                    status=status,
                    created_at=_now() - ago,
                )
            )
        await db.commit()


async def _age(
    factory, connector, *, statuses=COUNTED_STATUSES, by=timedelta(minutes=10)
):
    """Move these rows back in time, so they leave the post window (or the
    pause, or the held-card lookback)."""
    async with factory() as db:
        rows = (
            await db.execute(
                select(BotActionAudit).where(
                    BotActionAudit.connector_id == connector.id,
                    BotActionAudit.status.in_(statuses),
                )
            )
        ).scalars()
        for row in rows:
            row.created_at = _as_utc(row.created_at) - by
        await db.commit()


async def _audit(factory, connector) -> list[BotActionAudit]:
    async with factory() as db:
        return list(
            (
                await db.execute(
                    select(BotActionAudit)
                    .where(BotActionAudit.connector_id == connector.id)
                    .order_by(BotActionAudit.created_at)
                )
            ).scalars()
        )


async def _receipts(factory, incident_id):
    async with factory() as db:
        return list(
            await IncidentNotificationReceiptRepo.list_for_incident(
                db, ORG, incident_id
            )
        )


async def _stored(factory, connector) -> BotConnector:
    async with factory() as db:
        return await BotConnectorRepo.get_by_id(db, ORG, connector.id)


def _headline(card: dict) -> str:
    return card["body"][0]["text"]


def _buttons(card: dict) -> list[str]:
    return [action["title"] for action in card["actions"]]


def _facts(card: dict) -> dict[str, str]:
    fact_set = next(block for block in card["body"] if block["type"] == "FactSet")
    return {fact["title"]: fact["value"] for fact in fact_set["facts"]}


# -- One card per change -------------------------------------------------------------


async def test_each_incident_change_posts_one_card(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)

    await _event(factory, incident.id, "incident.created")
    await _set(factory, incident.id, status="in_progress")
    await _event(factory, incident.id, "incident.acknowledged")
    await _set(factory, incident.id, status="resolved")
    await _event(factory, incident.id, "incident.resolved")

    assert [_headline(card) for card in workflow.posted] == [
        "🚨 New incident: Checkout 500s",
        "✅ Incident acknowledged: Checkout 500s",
        "🟢 Incident resolved: Checkout 500s",
    ]
    assert [
        (row.command, row.status, row.chat_id)
        for row in await _audit(factory, connector)
    ] == [
        ("notify:incident.created", "ok", "workflow"),
        ("notify:incident.acknowledged", "ok", "workflow"),
        ("notify:incident.resolved", "ok", "workflow"),
    ]
    receipts = await _receipts(factory, incident.id)
    assert [
        (r.lifecycle_event, r.delivery_status, r.can_update, r.external_channel_id)
        for r in receipts
    ] == [
        ("incident.created", "accepted", False, "workflow"),
        ("incident.acknowledged", "accepted", False, "workflow"),
        ("incident.resolved", "accepted", False, "workflow"),
    ]
    assert all(r.external_message_id is None for r in receipts)
    assert (await _stored(factory, connector)).status == "healthy"


async def test_card_links_on_respond_and_none_after_resolve(factory, workflow):
    await _channel(factory)
    incident = await _incident(factory)

    await _event(factory, incident.id, "incident.created")
    created = workflow.posted[0]
    assert _buttons(created) == ALL_BUTTONS
    for action in created["actions"][:4]:
        assert action["url"].startswith(f"{PUBLIC}/actions/confirm#token=")
    assert LINKS_NOTE in json.dumps(created, ensure_ascii=False)
    # Org comes once, from the card facts.
    assert _facts(created)["Org"] == "Acme"

    await _set(factory, incident.id, status="resolved")
    await _event(factory, incident.id, "incident.resolved")
    assert _buttons(workflow.posted[1]) == VIEW_ONLY
    assert LINKS_NOTE not in json.dumps(workflow.posted[1], ensure_ascii=False)


async def test_moved_away_and_informational_posts_have_no_links(factory, workflow):
    payments = await _team(factory, "Payments")
    search = await _team(factory, "Search")
    await _channel(
        factory, config={"team_scope": "teams", "team_ids": [str(payments.id)]}
    )
    incident = await _incident(factory, team_id=payments.id)

    await _event(factory, incident.id, "incident.created")
    assert _buttons(workflow.posted[0]) == ALL_BUTTONS

    # Moved to another team with a status change: the channel it left gets one
    # last card, which names no team and carries no link.
    await _set(factory, incident.id, team_id=search.id, status="in_progress")
    await _event(
        factory,
        incident.id,
        "incident.updated",
        edit_only=True,
        previous_team_id=payments.id,
    )
    moved = workflow.posted[1]
    assert _buttons(moved) == VIEW_ONLY
    assert _facts(moved)["Team"] == "Moved to another team"

    # An informational post (a collision notice) never carries links.
    await notifier.deliver_incident_text(
        factory,
        org_id=ORG,
        text=(
            "*Possible duplicate of Checkout 500s*\n"
            f"Open incident: {PUBLIC}/dashboard/incidents/detail?id={incident.id}"
        ),
        event_type="incident.collision",
        team_id=payments.id,
        incident_id=incident.id,
        preserve_team=True,
        respond_only=True,
        strict_team_scope=True,
        informational_only=True,
    )
    assert len(workflow.posted) == 3
    assert _buttons(workflow.posted[2]) == VIEW_ONLY
    assert LINKS_NOTE not in json.dumps(workflow.posted[2], ensure_ascii=False)


async def test_escalation_text_posts_a_card_with_prebuilt_facts(factory, workflow):
    await _channel(factory)
    incident = await _incident(factory)
    facts = [
        ("Service", "checkout"),
        ("Responder", "Escalated to Sam Lee"),
        ("Escalation level", "1"),
    ]
    for event_type in ("incident.escalated", "incident.escalation_exhausted"):
        await notifier.deliver_incident_text(
            factory,
            org_id=ORG,
            text="*Escalation text the card does not use*",
            event_type=event_type,
            incident_id=incident.id,
            rendered_status="open",
            facts=facts,
            acknowledged_by=None,
        )

    escalated, exhausted = workflow.posted
    assert _headline(escalated) == "⏫ Incident escalated: Checkout 500s"
    assert _facts(escalated)["Responder"] == "Escalated to Sam Lee"
    assert _facts(escalated)["Escalation level"] == "1"
    assert _buttons(escalated) == ALL_BUTTONS
    assert _headline(exhausted) == "Escalation exhausted: Checkout 500s"
    assert exhausted["body"][1]["text"] == EXHAUSTED_NOTE
    assert EXHAUSTED_NOTE not in json.dumps(escalated, ensure_ascii=False)


async def test_escalation_before_commit_posts_a_text_card_with_view_button(
    factory, workflow
):
    await _channel(factory)
    # The incident is not readable yet (escalated before its commit).
    missing = uuid.uuid4()
    link = f"{PUBLIC}/dashboard/incidents/detail?id={missing}"
    await notifier.deliver_incident_text(
        factory,
        org_id=ORG,
        text=(
            "*⏫ Incident escalated: Checkout 500s*\n"
            "Severity: `critical` · Status: `open`\n\n"
            f"Open incident: {link}\n{SIGN_IN_TO_ACT_HINT}"
        ),
        event_type="incident.escalated",
        incident_id=missing,
        rendered_status="open",
    )
    [card] = workflow.posted
    assert card["body"][0] == {
        "type": "TextBlock",
        "wrap": True,
        "weight": "Bolder",
        "text": "⏫ Incident escalated: Checkout 500s",
    }
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": link}
    ]


async def test_collision_notice_posts_a_text_card(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    link = f"{PUBLIC}/dashboard/incidents/detail?id={incident.id}"
    await notifier.deliver_incident_text(
        factory,
        org_id=ORG,
        text=f"*Possible duplicate of Checkout 500s*\nOpen incident: {link}",
        event_type="incident.collision",
        incident_id=incident.id,
        respond_only=True,
        informational_only=True,
    )
    [card] = workflow.posted
    assert _headline(card) == "Possible duplicate of Checkout 500s"
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": link}
    ]
    assert [(r.command, r.status) for r in await _audit(factory, connector)] == [
        ("notify:incident.collision", "ok")
    ]
    assert await _receipts(factory, incident.id) == []


async def test_session_events_are_not_posted_to_workflows(
    factory, workflow, monkeypatch
):
    sent: list[str] = []

    async def telegram_send(*, bot_token, chat_id, text, **kwargs):
        sent.append(chat_id)
        return True, None

    monkeypatch.setattr("backend.bots.telegram.send_message", telegram_send)
    await _channel(factory)
    await _telegram(factory)
    incident = await _incident(factory)
    session = SimpleNamespace(
        id=uuid.uuid4(), incident_id=incident.id, status="active", summary=None
    )

    async def get_session(db, org_id, session_id):
        return session

    monkeypatch.setattr(notifier, "SessionRepo", SimpleNamespace(get_by_id=get_session))
    await notifier.deliver_session_chat_event(
        factory, org_id=ORG, event_type="session.created", session_id=session.id
    )

    # The event reached the other channel, so the skip is not an empty run.
    assert sent == ["-100777"]
    assert workflow.posted == []


async def test_ownership_refresh_posts_nothing(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _event(factory, incident.id, "incident.created")
    # Take, release, assign: the status stays, so nothing new is posted.
    await _event(factory, incident.id, "incident.updated", edit_only=True)
    assert len(workflow.posted) == 1
    assert len(await _audit(factory, connector)) == 1


async def test_reopen_posts_a_card(factory, workflow):
    await _channel(factory)
    incident = await _incident(factory)
    await _event(factory, incident.id, "incident.created")
    await _set(factory, incident.id, status="resolved")
    await _event(factory, incident.id, "incident.resolved")

    await _set(factory, incident.id, status="open")
    await _event(factory, incident.id, "incident.updated", edit_only=True)

    assert len(workflow.posted) == 3
    reopened = workflow.posted[2]
    assert _headline(reopened) == "✏️ Incident updated: Checkout 500s"
    assert _facts(reopened)["Status"] == "Open"
    assert _buttons(reopened) == ALL_BUTTONS


async def test_workflows_channel_is_delivered_after_other_channels(
    factory, workflow, monkeypatch
):
    order: list[str] = []
    workflow.order = order

    async def telegram_send(*, bot_token, chat_id, text, **kwargs):
        order.append("telegram")
        return True, None

    monkeypatch.setattr("backend.bots.telegram.send_message", telegram_send)
    # Listed first by name: the Workflows channel still goes last.
    await _channel(factory, name="a-teams-cards")
    await _telegram(factory)
    incident = await _incident(factory)

    await _event(factory, incident.id, "incident.created")
    await notifier.deliver_incident_text(
        factory,
        org_id=ORG,
        text="*Escalated*",
        event_type="incident.escalated",
        incident_id=incident.id,
        rendered_status="open",
    )
    assert order == ["telegram", "workflow", "telegram", "workflow"]


# -- The post limit -----------------------------------------------------------------


async def test_post_limit_holds_the_card_without_marking_the_channel_failed(
    factory, workflow
):
    connector = await _channel(factory)
    last_room = await _incident(factory, title="Last room")
    held = await _incident(factory, title="Held")
    await _seed(factory, connector, 19)

    # The 20th post goes out; the 21st is held.
    await _event(factory, last_room.id)
    assert len(workflow.posted) == 1
    await _event(factory, held.id)
    assert len(workflow.posted) == 1

    [row] = [r for r in await _audit(factory, connector) if r.status == "held"]
    assert row.incident_id == held.id
    assert row.command == "notify:incident.created"
    assert row.chat_id == "workflow"
    assert row.detail == (
        "OpsMender posted 20 times to this channel in the last 5 minutes, the most "
        "Microsoft Teams (Workflows) takes. OpsMender posts the newest state of this "
        "incident when there is room."
    )
    receipts = await _receipts(factory, held.id)
    assert [(r.lifecycle_event, r.delivery_status, r.can_update) for r in receipts] == [
        ("incident.created", "held", False)
    ]
    stored = await _stored(factory, connector)
    assert stored.status == "healthy"
    assert stored.last_error is None


async def test_counted_posts_include_failures_and_live_tests_not_inbound_or_old_rows(
    factory, workflow
):
    connector = await _channel(factory)
    other = await _channel(factory, name="b-other-cards")
    await _seed(factory, connector, 1, status="ok")
    await _seed(factory, connector, 1, status="delivery_failed")
    await _seed(factory, connector, 1, status="transient_failure")
    await _seed(factory, connector, 1, status="ok", command="live_test")
    await _seed(factory, connector, 1, status="held")
    await _seed(factory, connector, 1, status="limited")
    # An inbound command and a chat-button row are not posts.
    await _seed(factory, connector, 1, status="ok", command="/status")
    await _seed(
        factory, connector, 1, status="native_action_applied", command="acknowledge"
    )
    await _seed(factory, connector, 1, status="ok", command="acknowledge")
    # Outside the 5 minutes, or another channel's.
    await _seed(factory, connector, 1, status="ok", ago=timedelta(minutes=6))
    await _seed(factory, other, 3, status="ok")

    async with factory() as db:
        state = await channel_state(
            db, ORG, connector.id, budget_for("teams_workflows"), now=_now()
        )
    assert state.used == 4
    assert state.held == 1


async def test_over_limit_text_post_is_limited_and_never_replayed(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)
    await notifier.deliver_incident_text(
        factory,
        org_id=ORG,
        text="*Possible duplicate*",
        event_type="incident.collision",
        incident_id=incident.id,
        respond_only=True,
        informational_only=True,
    )
    assert workflow.posted == []
    [row] = [r for r in await _audit(factory, connector) if r.status == "limited"]
    assert row.command == "notify:incident.collision"
    assert row.detail.endswith("takes. This message was not posted.")
    assert await _receipts(factory, incident.id) == []

    await _age(factory, connector)
    assert await notifier.post_held_cards(factory) == 0
    assert workflow.posted == []


async def test_held_card_blocks_an_older_event(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)
    older = _now() - timedelta(seconds=5)

    await _event(factory, incident.id, "incident.acknowledged")
    await _age(factory, connector)
    # An event from before the held one, still in flight, is dropped.
    await _event(factory, incident.id, "incident.created", generated_at=older)
    assert workflow.posted == []


# -- Held cards -------------------------------------------------------------------------


async def test_held_changes_coalesce_into_one_card(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)

    await _set(factory, incident.id, status="in_progress")
    await _event(factory, incident.id, "incident.acknowledged")
    await _set(factory, incident.id, status="resolved")
    await _event(factory, incident.id, "incident.resolved")
    assert workflow.posted == []

    await _age(factory, connector)
    assert await notifier.post_held_cards(factory) == 1
    [card] = workflow.posted
    assert _headline(card) == "🟢 Incident resolved: Checkout 500s"
    assert _facts(card)["Status"] == "Resolved"
    assert _buttons(card) == VIEW_ONLY

    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1
    assert (await _receipts(factory, incident.id))[-1].delivery_status == "accepted"


async def test_catch_up_orders_open_then_priority_then_age_within_remaining_room(
    factory, workflow
):
    connector = await _channel(factory)
    await _seed(factory, connector, 20)
    old_p3 = await _incident(factory, title="Old P3", priority="P3")
    closed_p0 = await _incident(factory, title="Closed P0", priority="P0")
    open_p1 = await _incident(factory, title="Open P1", priority="P1")
    new_p0 = await _incident(factory, title="New P0", priority="P0")
    for incident in (old_p3, closed_p0, open_p1, new_p0):
        await _event(factory, incident.id)
    await _set(factory, closed_p0.id, status="resolved")
    assert workflow.posted == []

    await _age(factory, connector)
    await _seed(factory, connector, 18)
    assert await notifier.post_held_cards(factory) == 2
    assert [_headline(card) for card in workflow.posted] == [
        "🚨 New incident: New P0",
        "🚨 New incident: Open P1",
    ]


async def test_catch_up_skips_an_incident_whose_newer_card_went_out(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)
    await _event(factory, incident.id, "incident.created")
    await _age(factory, connector)

    await _set(factory, incident.id, status="in_progress")
    await _event(factory, incident.id, "incident.acknowledged")
    assert len(workflow.posted) == 1

    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1


async def test_catch_up_ignores_disabled_channels_and_holds_older_than_an_hour(
    factory, workflow
):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)
    await _event(factory, incident.id)
    await _age(factory, connector)

    async with factory() as db:
        stored = await BotConnectorRepo.get_by_id(db, ORG, connector.id)
        stored.is_enabled = False
        await db.commit()
    assert await notifier.post_held_cards(factory) == 0

    async with factory() as db:
        stored = await BotConnectorRepo.get_by_id(db, ORG, connector.id)
        stored.is_enabled = True
        await db.commit()
    await _age(factory, connector, statuses=("held",), by=timedelta(hours=2))
    assert await notifier.post_held_cards(factory) == 0
    assert workflow.posted == []


async def test_catch_up_drops_an_incident_no_longer_routed_here(factory, workflow):
    payments = await _team(factory, "Payments")
    search = await _team(factory, "Search")
    connector = await _channel(
        factory, config={"team_scope": "teams", "team_ids": [str(payments.id)]}
    )
    stays = await _incident(factory, title="Stays", team_id=payments.id)
    moves = await _incident(factory, title="Moves", priority="P0", team_id=payments.id)
    await _seed(factory, connector, 20)
    await _event(factory, stays.id)
    await _event(factory, moves.id)
    await _set(factory, moves.id, team_id=search.id)
    await _age(factory, connector)

    assert await notifier.post_held_cards(factory) == 2
    assert [_headline(card) for card in workflow.posted] == ["🚨 New incident: Stays"]
    assert (await _receipts(factory, moves.id))[-1].delivery_status == "dropped"

    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1


# -- Failures -------------------------------------------------------------------------------


async def test_transient_failure_holds_and_pauses_the_channel(factory, workflow):
    connector = await _channel(factory)
    first = await _incident(factory, title="First")
    second = await _incident(factory, title="Second")
    workflow.answers = [503]

    await _event(factory, first.id)
    assert len(workflow.posted) == 1
    [row] = await _audit(factory, connector)
    assert (row.status, row.incident_id) == ("transient_failure", first.id)
    assert row.detail == (
        "Teams Workflows did not answer (HTTP 503). OpsMender posts the newest "
        "state of this incident when the workflow answers."
    )
    assert [r.delivery_status for r in await _receipts(factory, first.id)] == ["held"]
    stored = await _stored(factory, connector)
    assert (stored.status, stored.last_error) == ("healthy", None)

    # Inside the 30 second pause a new card is held without a request.
    await _event(factory, second.id)
    assert len(workflow.posted) == 1
    held = (await _audit(factory, connector))[-1]
    assert held.status == "held"
    assert held.detail.startswith(
        "Microsoft Teams (Workflows) did not answer the last post, so OpsMender "
        "waits until "
    )
    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1

    await _age(
        factory, connector, statuses=("transient_failure",), by=timedelta(seconds=31)
    )
    assert await notifier.post_held_cards(factory) == 2
    assert [_headline(card) for card in workflow.posted[1:]] == [
        "🚨 New incident: First",
        "🚨 New incident: Second",
    ]


async def test_pause_doubles_and_marks_error_after_five(factory, workflow):
    connector = await _channel(factory)
    budget = budget_for("teams_workflows")
    for attempt, pause in enumerate(RETRY_PAUSES_SECONDS, start=1):
        workflow.answers = [503]
        incident = await _incident(factory, title=f"Try {attempt}")
        await _event(factory, incident.id)
        assert len(workflow.posted) == attempt

        async with factory() as db:
            state = await channel_state(db, ORG, connector.id, budget, now=_now())
            [latest] = await BotActionAuditRepo.latest_posts(
                db, ORG, connector.id, statuses=COUNTED_STATUSES, limit=1
            )
        assert state.transient_streak == attempt
        assert state.paused_until - _as_utc(latest.created_at) == timedelta(
            seconds=pause
        )
        stored = await _stored(factory, connector)
        assert stored.status == (
            "error" if attempt == len(RETRY_PAUSES_SECONDS) else "healthy"
        )
        await _age(
            factory,
            connector,
            statuses=("transient_failure",),
            by=timedelta(seconds=pause + 1),
        )
    assert stored.last_error == (
        "Teams Workflows did not answer (HTTP 503). 5 posts in a row did not go "
        "through; OpsMender keeps trying and posts held cards when the workflow "
        "answers."
    )


async def test_429_is_held_not_failed(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    workflow.answers = [429]
    await _event(factory, incident.id)

    [row] = await _audit(factory, connector)
    assert row.status == "transient_failure"
    assert row.detail.startswith("Teams Workflows is throttling posts (HTTP 429). ")
    assert [r.delivery_status for r in await _receipts(factory, incident.id)] == [
        "held"
    ]
    stored = await _stored(factory, connector)
    assert (stored.status, stored.last_error) == ("healthy", None)


async def test_permanent_failure_marks_the_channel_failed_and_stops_catch_up(
    factory, workflow
):
    connector = await _channel(factory)
    held = await _incident(factory, title="Held")
    refused = await _incident(factory, title="Refused")
    await _seed(factory, connector, 20)
    await _event(factory, held.id)
    await _age(factory, connector)

    workflow.answers = [404]
    await _event(factory, refused.id)
    assert len(workflow.posted) == 1
    rows = await _audit(factory, connector)
    assert rows[-1].status == "delivery_failed"
    stored = await _stored(factory, connector)
    assert stored.status == "error"
    assert stored.last_error.startswith(
        "Teams Workflows did not find the workflow (HTTP 404)"
    )
    assert await _receipts(factory, refused.id) == []

    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1


async def _failed_catch_up(factory, workflow):
    connector = await _channel(factory)
    incident = await _incident(factory)
    await _seed(factory, connector, 20)
    await _event(factory, incident.id)
    await _age(factory, connector)
    workflow.answers = [404]
    assert await notifier.post_held_cards(factory) == 1
    assert len(workflow.posted) == 1
    return connector, incident


async def test_failed_catch_up_is_not_retried_every_tick(factory, workflow):
    connector, incident = await _failed_catch_up(factory, workflow)
    assert (await _receipts(factory, incident.id))[-1].delivery_status == "held"
    assert (await _stored(factory, connector)).status == "error"

    assert await notifier.post_held_cards(factory) == 0
    assert await notifier.post_held_cards(factory) == 0
    assert len(workflow.posted) == 1


async def test_live_test_success_resumes_catch_up(factory, workflow):
    connector, incident = await _failed_catch_up(factory, workflow)
    await _age(
        factory, connector, statuses=("delivery_failed",), by=timedelta(seconds=1)
    )
    async with factory() as db:
        await BotActionAuditRepo.create(
            db,
            ORG,
            connector_id=connector.id,
            platform=connector.platform,
            chat_id="workflow",
            command="live_test",
            status="ok",
        )
        await db.commit()

    assert await notifier.post_held_cards(factory) == 1
    assert len(workflow.posted) == 2
    assert (await _receipts(factory, incident.id))[-1].delivery_status == "accepted"


# -- Concurrency ------------------------------------------------------------------------


async def test_concurrent_events_never_exceed_the_limit(factory, workflow):
    connector = await _channel(factory)
    incidents = [await _incident(factory, title=f"Incident {i}") for i in range(30)]

    await asyncio.gather(*(_event(factory, incident.id) for incident in incidents))

    assert len(workflow.posted) == 20
    statuses = [row.status for row in await _audit(factory, connector)]
    assert statuses.count("ok") == 20
    assert statuses.count("held") == 10


async def test_two_sweeps_post_each_held_card_once(factory, workflow):
    connector = await _channel(factory)
    await _seed(factory, connector, 20)
    for index in range(3):
        incident = await _incident(factory, title=f"Held {index}")
        await _event(factory, incident.id)
    await _age(factory, connector)

    await asyncio.gather(
        notifier.post_held_cards(factory), notifier.post_held_cards(factory)
    )
    assert sorted(_headline(card) for card in workflow.posted) == [
        "🚨 New incident: Held 0",
        "🚨 New incident: Held 1",
        "🚨 New incident: Held 2",
    ]


# -- Timeline, scheduler, My Routing and lanes ----------------------------------------


async def test_timeline_names_held_and_dropped_cards(factory):
    connector = await _channel(factory)
    incident = await _incident(factory)
    async with factory() as db:
        for status in ("held", "dropped", "accepted"):
            await IncidentNotificationReceiptRepo.create(
                db,
                ORG,
                incident_id=incident.id,
                connector_id=connector.id,
                platform="teams_workflows",
                lifecycle_event="incident.created",
                external_channel_id="workflow",
                delivery_status=status,
            )
        await db.commit()
        timeline = await get_incident_timeline(
            incident.id, db=db, org_id=ORG, user=None
        )
    notes = {
        item.status: item.title
        for item in timeline.items
        if item.lane == "notification"
    }
    assert notes == {
        "held": "Held for Teams Workflows",
        "dropped": "Not posted to Teams Workflows",
        "accepted": "Notified Teams Workflows",
    }


async def test_scheduler_runs_one_held_card_sweep_at_a_time(factory, monkeypatch):
    started: list[object] = []
    release = asyncio.Event()

    async def sweep(session_factory, *, now=None):
        started.append(session_factory)
        await release.wait()
        return 0

    monkeypatch.setattr(notifier, "post_held_cards", sweep)
    scheduler = EscalationScheduler(factory)
    scheduler._start_held_card_sweep()
    running = scheduler._held_cards
    scheduler._start_held_card_sweep()
    assert scheduler._held_cards is running
    await asyncio.sleep(0)
    assert started == [factory]

    await scheduler.stop()
    assert running.cancelled()
    assert scheduler._held_cards is None


async def test_scheduler_loop_restarts_completed_held_card_sweeps(factory, monkeypatch):
    tasks = []
    swept = asyncio.Event()

    async def tick():
        pass

    async def sweep(session_factory, *, now=None):
        assert session_factory is factory
        tasks.append(asyncio.current_task())
        if len(tasks) >= 3:
            swept.set()
        return 0

    monkeypatch.setattr(notifier, "post_held_cards", sweep)
    scheduler = EscalationScheduler(factory, poll_interval_seconds=0)
    monkeypatch.setattr(scheduler, "_tick", tick)
    await scheduler.start()
    try:
        await asyncio.wait_for(swept.wait(), timeout=3)
        assert len(set(tasks)) >= 3
    finally:
        await scheduler.stop()


async def test_newer_held_event_between_planning_and_delivery_is_replanned(
    factory, workflow, monkeypatch
):
    connector = await _channel(factory)
    await _seed(factory, connector, 20)
    incident = await _incident(factory)
    await _event(factory, incident.id)
    await _age(factory, connector)
    deliver = notifier.deliver_incident_event

    async def newer_event(*args, **kwargs):
        await _seed(factory, connector, 20)
        await _event(factory, incident.id, "incident.escalated")
        await _age(factory, connector)
        # The newer hold lands before the sweep captures its send timestamp.
        # The timestamp stale check alone therefore cannot catch this race.
        kwargs["generated_at"] = _now()
        return await deliver(*args, **kwargs)

    monkeypatch.setattr(notifier, "deliver_incident_event", deliver)

    # Insert the newer event after the sweep planned the old headline.
    async def interleave(*args, **kwargs):
        monkeypatch.setattr(notifier, "deliver_incident_event", deliver)
        return await newer_event(*args, **kwargs)

    monkeypatch.setattr(notifier, "deliver_incident_event", interleave)
    assert await notifier.post_held_cards(factory) == 1
    assert workflow.posted == []
    assert (await _receipts(factory, incident.id))[-1].delivery_status == "held"
    assert await notifier.post_held_cards(factory) == 1
    assert _headline(workflow.posted[0]).startswith("⏫ Incident escalated:")


async def test_my_routing_stage_on_workflows_is_skipped(factory, workflow):
    # Even with a chat id saved in its config, the stage is not sent.
    connector = await _channel(factory, config={"allowed_chat_ids": ["workflow"]})
    incident = await _incident(factory)
    async with factory() as db:
        user = await UserRepo.create(
            db,
            username="sam",
            email="sam@test.com",
            password_hash="x",
            role="operator",
            primary_org_id=ORG,
        )
        await db.commit()

    sender = ne.build_notification_sender(lambda wanted: None)
    async with factory() as db:
        outcome = await sender(
            db,
            ORG,
            channel_id=str(connector.id),
            incident=incident,
            user=user,
            subject="P1 page",
            body="Checkout 500s",
        )
    assert outcome == ("skipped", "channel_posts_only")
    assert workflow.posted == []


def test_skip_reason_has_a_timeline_label():
    assert CHANNEL_POSTS_ONLY_SKIP_REASON == "channel_posts_only"
    assert _SKIP_REASONS[CHANNEL_POSTS_ONLY_SKIP_REASON] == (
        "a Microsoft Teams (Workflows) channel takes incident cards, not personal pages"
    )


async def test_track_lane_is_refused_for_workflows(factory, workflow):
    for lanes in (["track"], ["respond", "track"]):
        with pytest.raises(HTTPException) as exc:
            _validate_platform_lanes("teams_workflows", lanes)
        assert exc.value.status_code == 400
    assert _validate_platform_lanes("teams_workflows", ["respond"]) == ["respond"]

    # A channel saved with Track before the API refused it gets nothing.
    await _channel(factory, lanes=["track"])
    incident = await _incident(factory)
    assert await _event(factory, incident.id) == 0
    assert workflow.posted == []
