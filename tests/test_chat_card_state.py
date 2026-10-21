"""M1-73: the chat incident card says who holds the incident and stays current
after a chat action.

Every native card (Slack, Teams, Discord) shows the facts the text card shows,
from one helper. A verified chat Acknowledge or Resolve re-renders the card in
the channel through the same incident event the web path emits: Slack and
Discord edit the posted message in place, Teams posts a fresh card. Escalate
keeps the buttons. Slack answers the person who tapped through the payload's
``response_url``. No real workspace is contacted: every outbound HTTP call
lands in a mock transport.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.bots import notifier
from backend.bots.incident_card import build_incident_message, incident_card_facts
from backend.config_loader import set_env_path
from backend.db.models import Base, BotActionAudit, IncidentPage, Organization
from backend.db.repos import (
    BotConnectorRepo,
    BotUserLinkRepo,
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentNotificationReceiptRepo,
    IncidentPageRepo,
    IncidentRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as _esc
from backend.paging.slack_cards import (
    ACTION_ACK,
    ACTION_ESCALATE,
    ACTION_RESOLVE,
    ACTION_START_AI_SESSION,
    ACTION_VIEW,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000c73")
PUBLIC_URL = "https://ops.example.com"
SIGNING_SECRET = "chat-card-signing-secret"
SLACK_CHANNEL = "C123"
SLACK_TS = "1700000000.000100"
RESPONSE_URL = "https://hooks.slack.com/actions/T1/1/abc"
DISCORD_CHANNEL = "discord-channel"
DISCORD_MESSAGE = "discord-message"
TEAMS_CHAT = "19:chat@thread.v2"
BOT_APP_ID = "11111111-1111-1111-1111-111111111111"

_RealAsyncClient = httpx.AsyncClient


# ---------------------------------------------------------------------------
# One list of facts for every card
# ---------------------------------------------------------------------------


def _stub_incident(**overrides):
    base = dict(
        id=uuid.uuid4(),
        title="Checkout 500s",
        description="Spike in 500s",
        status="open",
        severity="critical",
        priority="P1",
        external_source="cloudwatch",
        created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


ACKED = {
    "responder_state": "assigned",
    "responder_display_name": "Sam Ops",
    "acknowledged_by_display_name": "Sam Ops",
    "escalation_level": 2,
    "previous_responder_display_name": "Al Pha",
}


def test_facts_name_the_holder_in_card_order():
    facts = incident_card_facts(
        _stub_incident(),
        responder=ACKED,
        service_name="Checkout API",
        team_name="Payments",
        org_name="Chat Org",
    )
    assert facts == [
        ("Service", "Checkout API"),
        ("Team", "Payments"),
        ("Org", "Chat Org"),
        ("Responder", "Assigned to Sam Ops"),
        ("Acknowledged by", "Sam Ops"),
        ("Escalation level", "2"),
    ]


def test_facts_leave_out_what_is_not_set():
    incident = _stub_incident()
    assert incident_card_facts(
        incident, responder=None, service_name=None, team_name=None, org_name=None
    ) == [("Service", "cloudwatch")]
    awaiting = {
        "responder_state": "awaiting",
        "responder_display_name": "Al Pha",
        "acknowledged_by_display_name": None,
        "escalation_level": 0,
    }
    assert incident_card_facts(
        incident, responder=awaiting, service_name=None, team_name=None, org_name=None
    ) == [("Service", "cloudwatch"), ("Responder", "Awaiting Al Pha")]
    assert incident_card_facts(
        incident,
        responder={"responder_state": "unassigned"},
        service_name="api",
        team_name=None,
        org_name=None,
    ) == [("Service", "api"), ("Responder", "Unassigned")]


def test_text_card_shows_the_same_facts():
    incident = _stub_incident()
    card = dict(
        responder=ACKED,
        service_name="Checkout API",
        team_name="Payments",
        org_name="Chat Org",
    )
    text = build_incident_message(
        incident,
        event_type="incident.acknowledged",
        base_url=PUBLIC_URL,
        supports_actions=False,
        **card,
    )
    lines = text.splitlines()
    assert "Service/source: Checkout API · Team: Payments · Org: Chat Org" in lines
    for label, value in incident_card_facts(incident, **card)[3:]:
        assert f"{label}: {value}" in lines
    assert "Previous responder: Al Pha" in lines
    assert "Sign in to OpsMender" in text
    assert "token=" not in text


# ---------------------------------------------------------------------------
# App, outbound HTTP and background delivery fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSMENDER_PUBLIC_URL", PUBLIC_URL)
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'chat-card.db'}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Chat Org", slug="chat-org"))
        await session.commit()
    set_session_factory(factory)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(env_file)

    application = create_app()
    application.state.session_factory = factory

    class _Pool:
        async def get_server(self, *args, **kwargs):
            return object()

        @asynccontextmanager
        async def connect(self, *args, **kwargs):
            yield object()

    set_mcp_pool(_Pool())

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db
    yield application

    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


class _Outbound:
    """Every request the adapters make, answered the way each platform does."""

    def __init__(self):
        self.requests: list[tuple[str, str, object]] = []
        self.fail_hooks = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        url = urllib.parse.unquote(str(request.url))
        self.requests.append((request.method, url, body))
        host = request.url.host
        if host == "hooks.slack.com":
            if self.fail_hooks:
                raise httpx.ConnectError(f"cannot reach {url}", request=request)
            return httpx.Response(200, text="ok")
        if host == "slack.com":
            ts = body.get("ts") or "1700000099.000200"
            return httpx.Response(
                200, json={"ok": True, "channel": body["channel"], "ts": ts}
            )
        if host == "discord.com":
            return httpx.Response(
                200, json={"id": DISCORD_MESSAGE, "channel_id": DISCORD_CHANNEL}
            )
        if host == "graph.microsoft.com":
            return httpx.Response(201, json={"id": "teams-message-2"})
        return httpx.Response(404)

    def to(self, fragment: str) -> list[tuple[str, str, object]]:
        return [item for item in self.requests if fragment in item[1]]


@pytest.fixture
def outbound(monkeypatch):
    recorder = _Outbound()
    transport = httpx.MockTransport(recorder.handler)

    def _client(*args, **kwargs):
        return _RealAsyncClient(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    return recorder


@pytest.fixture
def card_tasks(monkeypatch):
    """The incident-event deliveries chat actions schedule, to await them."""
    tasks: list[asyncio.Task] = []
    original = notifier.schedule_incident_event

    def _record(*args, **kwargs):
        task = original(*args, **kwargs)
        tasks.append(task)
        return task

    monkeypatch.setattr(notifier, "schedule_incident_event", _record)
    return tasks


async def _drain(tasks: list[asyncio.Task]) -> None:
    await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


async def _seed(
    app,
    *,
    platform: str,
    credentials: dict,
    config: dict,
    external_user_id: str,
    first_name: str,
    prior_channel: str,
    prior_message: str,
    can_update: bool,
):
    """A Payments team incident on Checkout API, a linked operator on the
    team, a native-action channel and the card it already posted."""
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="Payments", slug="payments")
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name="Checkout API",
            slug="checkout-api",
        )
        user = await UserRepo.create(
            db,
            username=f"{platform}-{first_name.lower()}",
            email=f"{first_name.lower()}@{platform}.example",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
            first_name=first_name,
            last_name="Ops",
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role="operator"
        )
        await TeamRepo.add_member(db, TEST_ORG_ID, team.id, user_id=user.id)
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=f"{platform}-cards",
            platform=platform,
            config=config,
            credentials=credentials,
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        connector.callback_status = "verified"
        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id=external_user_id,
            opsmender_user_id=user.id,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout 500s",
            description="Spike in 500s",
            severity="high",
            priority="P1",
            response_mode="page",
            service_id=service.id,
        )
        await IncidentNotificationReceiptRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            connector_id=connector.id,
            platform=platform,
            lifecycle_event="incident.created",
            external_channel_id=prior_channel,
            external_message_id=prior_message,
            rendered_status="open",
            can_update=can_update,
        )
        await db.commit()
        return SimpleNamespace(
            connector_id=connector.id,
            incident_id=incident.id,
            user_id=user.id,
            team_id=team.id,
        )


async def _seed_slack(app):
    return await _seed(
        app,
        platform="slack",
        credentials={"signing_secret": SIGNING_SECRET, "bot_token": "xoxb-cards"},
        config={"default_chat_id": SLACK_CHANNEL},
        external_user_id="U_SAM",
        first_name="Sam",
        prior_channel=SLACK_CHANNEL,
        prior_message=SLACK_TS,
        can_update=True,
    )


def _slack_request(action_id: str, incident_id, *, action_ts="1700000001.000001"):
    payload = {
        "type": "block_actions",
        "user": {"id": "U_SAM", "name": "sam"},
        "channel": {"id": SLACK_CHANNEL},
        "message": {"ts": SLACK_TS},
        "response_url": RESPONSE_URL,
        "actions": [
            {
                "action_id": action_id,
                "block_id": f"opsmender:incident:{incident_id}:actions",
                "value": str(incident_id),
                "action_ts": action_ts,
            }
        ],
    }
    body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            SIGNING_SECRET.encode(),
            f"v0:{timestamp}:{body.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    return body, {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _action_ids(blocks: list[dict]) -> list[str]:
    actions = [block for block in blocks if block["type"] == "actions"]
    return [element["action_id"] for element in actions[0]["elements"]]


def _fields(blocks: list[dict]) -> list[str]:
    return [
        field["text"]
        for block in blocks
        if block["type"] == "section" and "fields" in block
        for field in block["fields"]
    ]


def _context(blocks: list[dict]) -> str:
    return next(b for b in blocks if b["type"] == "context")["elements"][0]["text"]


async def _receipts(app, incident_id):
    async with app.state.session_factory() as db:
        return list(
            await IncidentNotificationReceiptRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
        )


async def _owner(app, incident_id):
    async with app.state.session_factory() as db:
        active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
        return active.assigned_to if active is not None else None


async def _page_count(app, incident_id) -> int:
    async with app.state.session_factory() as db:
        return (
            await db.execute(
                select(func.count())
                .select_from(IncidentPage)
                .where(IncidentPage.incident_id == incident_id)
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


async def test_slack_acknowledge_edits_the_channel_card_in_place(
    client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    pages_before = await _page_count(app, seeded.incident_id)
    body, headers = _slack_request(ACTION_ACK, seeded.incident_id)

    resp = await client.post("/bot/slack/interactions", content=body, headers=headers)
    await _drain(card_tasks)

    # Slack ignores the body of a block_actions response: an empty 200, and
    # the answer goes to the response_url, visible only to the person.
    assert resp.status_code == 200
    assert resp.content == b""
    answers = outbound.to("hooks.slack.com")
    assert [(method, url) for method, url, _ in answers] == [("POST", RESPONSE_URL)]
    assert answers[0][2] == {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": "acknowledged incident *Checkout 500s*.",
    }

    # The card already in the channel is edited in place, not posted again.
    assert outbound.to("chat.postMessage") == []
    edits = outbound.to("chat.update")
    assert len(edits) == 1
    edit = edits[0][2]
    assert edit["channel"] == SLACK_CHANNEL
    assert edit["ts"] == SLACK_TS
    blocks = edit["blocks"]
    assert _context(blocks) == (
        f"Acknowledged by Sam Ops · Incident `{seeded.incident_id}`"
    )
    assert _fields(blocks) == [
        "*Service*\nCheckout API",
        "*Team*\nPayments",
        "*Org*\nChat Org",
        "*Responder*\nAssigned to Sam Ops",
        "*Acknowledged by*\nSam Ops",
    ]
    assert _action_ids(blocks) == [
        ACTION_RESOLVE,
        ACTION_ESCALATE,
        ACTION_START_AI_SESSION,
        ACTION_VIEW,
    ]
    view = next(
        e
        for b in blocks
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"] == ACTION_VIEW
    )
    assert view["url"].startswith(f"{PUBLIC_URL}/dashboard/incidents/detail?id=")
    assert view["url"].endswith("from=slack")

    # Saved state: the operator holds it, the edit is recorded on the same
    # message, and nobody was paged by the refresh.
    assert await _owner(app, seeded.incident_id) == seeded.user_id
    receipts = await _receipts(app, seeded.incident_id)
    assert [r.lifecycle_event for r in receipts] == [
        "incident.created",
        "incident.acknowledged",
    ]
    assert receipts[-1].delivery_status == "updated"
    assert receipts[-1].external_message_id == SLACK_TS
    assert await _page_count(app, seeded.incident_id) == pages_before


async def test_slack_replayed_click_refreshes_the_card_once(
    client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    body, headers = _slack_request(ACTION_ACK, seeded.incident_id)

    first = await client.post("/bot/slack/interactions", content=body, headers=headers)
    await _drain(card_tasks)
    replay = await client.post("/bot/slack/interactions", content=body, headers=headers)
    await _drain(card_tasks)

    assert first.status_code == replay.status_code == 200
    assert len(outbound.to("chat.update")) == 1
    assert len(outbound.to("hooks.slack.com")) == 2
    async with app.state.session_factory() as db:
        audits = (
            (
                await db.execute(
                    select(BotActionAudit.status).where(
                        BotActionAudit.incident_id == seeded.incident_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert audits.count("native_action_applied") == 1
    assert audits.count("native_action_deduplicated") == 1


async def test_slack_answer_failure_does_not_undo_the_action(
    client, app, outbound, card_tasks, caplog
):
    seeded = await _seed_slack(app)
    outbound.fail_hooks = True
    body, headers = _slack_request(ACTION_RESOLVE, seeded.incident_id)

    with caplog.at_level("WARNING", logger="backend.api.routes.slack_paging"):
        resp = await client.post(
            "/bot/slack/interactions", content=body, headers=headers
        )
    await _drain(card_tasks)

    assert resp.status_code == 200
    assert resp.content == b""
    assert len(outbound.to("hooks.slack.com")) == 1
    # Logged without the response_url, which works as a credential until it
    # expires.
    assert "Slack response_url answer failed" in caplog.text
    assert RESPONSE_URL not in caplog.text
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, seeded.incident_id)
        applied = (
            await db.execute(
                select(func.count())
                .select_from(BotActionAudit)
                .where(
                    BotActionAudit.incident_id == seeded.incident_id,
                    BotActionAudit.status == "native_action_applied",
                )
            )
        ).scalar_one()
    assert incident.status == "resolved"
    assert applied == 1
    # The resolved card keeps only the link back to OpsMender.
    edits = outbound.to("chat.update")
    assert len(edits) == 1
    assert _action_ids(edits[0][2]["blocks"]) == [ACTION_VIEW]


async def test_slack_escalate_keeps_the_four_actions(
    client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    async with app.state.session_factory() as db:
        bea = await UserRepo.create(
            db,
            username="bea",
            email="bea@slack.example",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
            first_name="Bea",
            last_name="Two",
        )
        await UserRepo.add_to_organization(
            db, user_id=bea.id, org_id=TEST_ORG_ID, role="operator"
        )
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=seeded.team_id, name="payments-chain"
        )
        for index, target in enumerate((seeded.user_id, bea.id)):
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=index,
                target_type="user",
                target_id=target,
            )
        await _esc.start_chain(
            db, TEST_ORG_ID, incident_id=seeded.incident_id, chain_id=chain.id
        )
        await db.commit()

    # The escalation engine schedules its card post before the request
    # commits; hold it until the request is done, then run it as scheduled.
    held: list[tuple[tuple, dict]] = []
    original = notifier.schedule_incident_text
    monkeypatch.setattr(
        notifier,
        "schedule_incident_text",
        lambda *args, **kwargs: held.append((args, kwargs)),
    )
    body, headers = _slack_request(ACTION_ESCALATE, seeded.incident_id)

    resp = await client.post("/bot/slack/interactions", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.content == b""
    assert outbound.to("hooks.slack.com")[0][2]["text"] == (
        "escalated incident *Checkout 500s*."
    )
    assert len(held) == 1
    args, kwargs = held[0]
    await original(*args, **kwargs)
    await _drain(card_tasks)

    edits = outbound.to("chat.update")
    assert len(edits) == 1
    assert edits[0][2]["ts"] == SLACK_TS
    blocks = edits[0][2]["blocks"]
    assert _action_ids(blocks) == [
        ACTION_ACK,
        ACTION_RESOLVE,
        ACTION_ESCALATE,
        ACTION_START_AI_SESSION,
        ACTION_VIEW,
    ]
    assert "*Responder*\nEscalated to Bea Two" in _fields(blocks)
    assert "*Escalation level*\n1" in _fields(blocks)

    async with app.state.session_factory() as db:
        paged = await IncidentPageRepo.list_for_incident(
            db, TEST_ORG_ID, seeded.incident_id
        )
    assert any(p.user_id == bea.id and p.step_index == 1 for p in paged)
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.escalated"
    assert receipts[-1].delivery_status == "updated"


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


async def test_discord_acknowledge_patches_the_channel_message(
    client, app, outbound, card_tasks
):
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    seeded = await _seed(
        app,
        platform="discord",
        credentials={"public_key": public_key.hex(), "bot_token": "discord-token"},
        config={"default_chat_id": DISCORD_CHANNEL},
        external_user_id="discord-dee",
        first_name="Dee",
        prior_channel=DISCORD_CHANNEL,
        prior_message=DISCORD_MESSAGE,
        can_update=True,
    )
    payload = {
        "type": 3,
        "id": "discord-interaction-ack",
        "application_id": "discord-app",
        "token": "interaction-token",
        "channel_id": DISCORD_CHANNEL,
        "member": {"user": {"id": "discord-dee", "username": "dee"}},
        "data": {"component_type": 2, "custom_id": "opsmender:ack"},
        "message": {
            "id": DISCORD_MESSAGE,
            "embeds": [
                {"footer": {"text": f"OpsMender incident {seeded.incident_id}"}}
            ],
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "1720000000"
    resp = await client.post(
        f"/bot-connectors/{seeded.connector_id}/discord/webhook",
        content=body,
        headers={
            "X-Signature-Ed25519": signing_key.sign(timestamp.encode() + body).hex(),
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    await _drain(card_tasks)

    assert resp.json() == {"type": 5, "data": {"flags": 64}}
    followups = outbound.to("/webhooks/discord-app/interaction-token")
    assert followups[0][2] == {"content": "acknowledged incident *Checkout 500s*."}
    edits = [
        item
        for item in outbound.to(f"/channels/{DISCORD_CHANNEL}/messages/")
        if item[0] == "PATCH"
    ]
    assert len(edits) == 1
    assert edits[0][1].endswith(f"/messages/{DISCORD_MESSAGE}")
    message = edits[0][2]
    assert message["content"].startswith("**✅ Incident acknowledged: Checkout 500s**")
    embed = message["embeds"][0]
    assert {"name": "Responder", "value": "Assigned to Dee Ops", "inline": True} in (
        embed["fields"]
    )
    assert {"name": "Acknowledged by", "value": "Dee Ops", "inline": True} in (
        embed["fields"]
    )
    assert embed["footer"]["text"].endswith(str(seeded.incident_id))
    assert [b["custom_id"] for b in message["components"][0]["components"]] == [
        "opsmender:resolve",
        "opsmender:escalate",
        "opsmender:start_ai_session",
    ]

    assert await _owner(app, seeded.incident_id) == seeded.user_id
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.acknowledged"
    assert receipts[-1].delivery_status == "updated"
    assert receipts[-1].external_message_id == DISCORD_MESSAGE


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


async def test_teams_acknowledge_posts_a_fresh_card(
    client, app, outbound, card_tasks, monkeypatch
):
    async def _verified(**kwargs):
        return None

    async def _token(**kwargs):
        return SimpleNamespace(token_type="Bearer", access_token="graph-token")

    monkeypatch.setattr(
        "backend.api.routes.teams_paging.verify_bot_framework_token", _verified
    )
    monkeypatch.setattr("backend.bots.connectors.teams.acquire_app_only_token", _token)
    seeded = await _seed(
        app,
        platform="teams",
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret",
        },
        config={"bot_app_id": BOT_APP_ID, "default_chat_id": TEAMS_CHAT},
        external_user_id="aad-tia",
        first_name="Tia",
        prior_channel=TEAMS_CHAT,
        prior_message="teams-message-1",
        can_update=False,
    )
    resp = await client.post(
        "/bot/teams/activity",
        json={
            "type": "message",
            "id": "teams-activity-ack",
            "recipient": {"id": f"28:{BOT_APP_ID}"},
            "from": {"id": "29:tia", "aadObjectId": "aad-tia", "name": "Tia"},
            "conversation": {"id": TEAMS_CHAT},
            "replyToId": "teams-message-1",
            "value": {
                "action": "opsmender:ack",
                "incident_id": str(seeded.incident_id),
            },
        },
    )
    await _drain(card_tasks)

    assert resp.json() == {
        "type": "message",
        "text": "acknowledged incident *Checkout 500s*.",
    }
    posts = outbound.to(f"/chats/{TEAMS_CHAT}/messages")
    assert len(posts) == 1
    card = posts[0][2]["attachments"][0]["content"]
    facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
    kv = {fact["title"]: fact["value"] for fact in facts}
    assert kv["Responder"] == "Assigned to Tia Ops"
    assert kv["Acknowledged by"] == "Tia Ops"
    assert kv["Service"] == "Checkout API"
    assert kv["Team"] == "Payments"
    assert [action["title"] for action in card["actions"]] == [
        "Resolve",
        "Escalate",
        "Start AI Session",
        "View in OpsMender",
    ]

    assert await _owner(app, seeded.incident_id) == seeded.user_id
    receipts = await _receipts(app, seeded.incident_id)
    assert [r.lifecycle_event for r in receipts] == [
        "incident.created",
        "incident.acknowledged",
    ]
    assert receipts[-1].external_message_id == "teams-message-2"
    assert receipts[-1].can_update is False
