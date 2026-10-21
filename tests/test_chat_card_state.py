"""M1-73: the chat incident card says who holds the incident and stays current
after a chat action.

Every native card (Slack, Teams, Discord) shows the facts the text card shows,
from one helper. A verified chat Acknowledge or Resolve re-renders the card in
the channel through the same incident event the web path emits: Slack and
Discord edit the posted message in place, Teams posts a fresh card. Escalate
keeps the buttons; Start AI Session and taps that change nothing leave the
card alone. The Slack `/ack`, `/take`, `/resolve` and `/release` commands and
the phone keypad update it as a tap would. Every other ownership change (a
release, take, assignment, reassignment, handoff, bulk change, service move,
reopen or session override) edits the card in place and posts nothing new.
When the incident moves to another team, a channel limited to the team it
left gets one last edit that names no one and has no buttons, then nothing.
Slack answers the person who tapped through the payload's ``response_url``,
after the request's work is committed. No real workspace is contacted: every outbound HTTP call
lands in a mock transport.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
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
from backend.api.auth import hash_password
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.bots import notifier
from backend.bots.connectors.slack import SlackAdapter
from backend.bots.incident_card import (
    SIGN_IN_TO_ACT_HINT,
    build_incident_message,
    incident_card_facts,
)
from backend.config_loader import set_env_path
from backend.db.models import (
    Base,
    BotActionAudit,
    BotConnector,
    IncidentPage,
    Organization,
)
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
    SessionRepo,
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
PASSWORD = "chat-card-password-1"

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
        # Run while Slack is being answered, to see what is durable by then.
        self.probe = None
        self.probed: list[object] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        url = urllib.parse.unquote(str(request.url))
        self.requests.append((request.method, url, body))
        host = request.url.host
        if host == "hooks.slack.com":
            if self.fail_hooks:
                raise httpx.ConnectError(f"cannot reach {url}", request=request)
            if self.probe is not None:
                return self._probe_then_answer()
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

    async def _probe_then_answer(self) -> httpx.Response:
        self.probed.append(await self.probe())
        return httpx.Response(200, text="ok")

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


def _slack_request(
    action_id: str,
    incident_id,
    *,
    action_ts="1700000001.000001",
    user: dict | None = None,
):
    payload = {
        "type": "block_actions",
        "user": {"id": "U_SAM", "name": "sam"} if user is None else user,
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
    return body, _signed(body)


def _slack_command(command: str, text: str):
    body = urllib.parse.urlencode(
        {"command": command, "text": text, "user_id": "U_SAM"}
    ).encode()
    return body, _signed(body)


def _signed(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(
            SIGNING_SECRET.encode(),
            f"v0:{timestamp}:{body.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    return {
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


async def _audit_statuses(app, incident_id) -> list[str]:
    async with app.state.session_factory() as db:
        return list(
            (
                await db.execute(
                    select(BotActionAudit.status).where(
                        BotActionAudit.incident_id == incident_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _add_teams_channel(app, monkeypatch) -> None:
    """A Teams Notification Channel beside the Slack one. Teams cannot edit a
    posted card, so every card it gets is a new post."""

    async def _token(**kwargs):
        return SimpleNamespace(token_type="Bearer", access_token="graph-token")

    monkeypatch.setattr("backend.bots.connectors.teams.acquire_app_only_token", _token)
    async with app.state.session_factory() as db:
        await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="teams-cards",
            platform="teams",
            config={"bot_app_id": BOT_APP_ID, "default_chat_id": TEAMS_CHAT},
            credentials={
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "secret",
            },
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()


def _teams_posts(outbound) -> int:
    return len(outbound.to(f"/chats/{TEAMS_CHAT}/messages"))


async def _seed_chain(app, seeded) -> uuid.UUID:
    """A running Escalation Chain: Sam at level 0 (paged now), Bea at 1."""
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
        return bea.id


async def _login(client, app, user_id) -> dict[str, str]:
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        user.password_hash = hash_password(PASSWORD)
        username = user.username
        await db.commit()
    resp = await client.post(
        "/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _tap(client, action_id: str, incident_id, **kwargs):
    body, headers = _slack_request(action_id, incident_id, **kwargs)
    return await client.post("/bot/slack/interactions", content=body, headers=headers)


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
    # The resolved card keeps only the link back to OpsMender, under the
    # resolved headline the web's resolve event gives it.
    edits = outbound.to("chat.update")
    assert len(edits) == 1
    assert _action_ids(edits[0][2]["blocks"]) == [ACTION_VIEW]
    assert edits[0][2]["text"].startswith("*🟢 Incident resolved: Checkout 500s*")
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.resolved"


async def test_slack_answer_that_raises_is_logged_and_the_action_stays(
    client, app, outbound, card_tasks, caplog, monkeypatch
):
    """The background answer catches whatever the adapter raises: the person
    who tapped already has the empty 200, the failure is logged by its type,
    and the action stays applied."""
    seeded = await _seed_slack(app)

    async def _raise(self, *, response_url, text):
        raise RuntimeError(f"cannot answer {response_url}")

    monkeypatch.setattr(SlackAdapter, "respond_to_interaction", _raise)
    with caplog.at_level("WARNING", logger="backend.api.routes.slack_paging"):
        resp = await _tap(client, ACTION_RESOLVE, seeded.incident_id)
    await _drain(card_tasks)

    assert resp.status_code == 200
    assert resp.content == b""
    assert "Slack response_url answer failed: RuntimeError" in caplog.text
    assert RESPONSE_URL not in caplog.text
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, seeded.incident_id)
    assert incident.status == "resolved"
    assert (await _audit_statuses(app, seeded.incident_id)).count(
        "native_action_applied"
    ) == 1


async def test_slack_answer_never_logs_the_response_url(
    client, app, outbound, card_tasks, caplog
):
    """The HTTP client logs each request's URL at INFO, the shipped level.
    A response_url works as a credential until it expires, so a normal,
    successful answer must not write it to the log either."""
    seeded = await _seed_slack(app)

    with caplog.at_level(logging.INFO):
        resp = await _tap(client, ACTION_ACK, seeded.incident_id)
        await _drain(card_tasks)

    assert resp.status_code == 200
    assert outbound.to("hooks.slack.com")[0][1] == RESPONSE_URL
    assert len(outbound.to("chat.update")) == 1
    assert RESPONSE_URL not in caplog.text
    assert "/actions/T1/1/" not in caplog.text


async def test_slack_escalate_keeps_the_four_actions(
    client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    bea_id = await _seed_chain(app, seeded)

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
    # The escalation text names the org, like every other incident message.
    assert "Org: Chat Org" in kwargs["text"]
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
    assert "*Org*\nChat Org" in _fields(blocks)
    assert "*Responder*\nEscalated to Bea Two" in _fields(blocks)
    assert "*Escalation level*\n1" in _fields(blocks)

    async with app.state.session_factory() as db:
        paged = await IncidentPageRepo.list_for_incident(
            db, TEST_ORG_ID, seeded.incident_id
        )
    assert any(p.user_id == bea_id and p.step_index == 1 for p in paged)
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.escalated"
    assert receipts[-1].delivery_status == "updated"


async def test_slack_escalate_after_acknowledge_keeps_the_acknowledger(
    client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    await _seed_chain(app, seeded)
    held: list[tuple[tuple, dict]] = []
    original = notifier.schedule_incident_text
    monkeypatch.setattr(
        notifier,
        "schedule_incident_text",
        lambda *args, **kwargs: held.append((args, kwargs)),
    )

    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    resp = await _tap(
        client, ACTION_ESCALATE, seeded.incident_id, action_ts="1700000002.000002"
    )
    assert resp.status_code == 200
    assert len(held) == 1
    args, kwargs = held[0]
    await original(*args, **kwargs)
    await _drain(card_tasks)

    # Escalating keeps the owner's acknowledgement, and the edited card says so.
    assert await _owner(app, seeded.incident_id) == seeded.user_id
    edits = outbound.to("chat.update")
    assert len(edits) == 2
    blocks = edits[-1][2]["blocks"]
    assert _context(blocks).startswith("Acknowledged by Sam Ops · ")
    assert _action_ids(blocks) == [
        ACTION_RESOLVE,
        ACTION_ESCALATE,
        ACTION_START_AI_SESSION,
        ACTION_VIEW,
    ]
    assert "*Escalation level*\n1" in _fields(blocks)


async def test_slack_answer_follows_the_committed_escalation(
    client, app, outbound, card_tasks, monkeypatch
):
    """Escalate commits nothing on its own before the answer. Slack is
    answered only once the escalation is durable: another session already
    sees the new level's page and the audit."""
    seeded = await _seed_slack(app)
    bea_id = await _seed_chain(app, seeded)
    monkeypatch.setattr(notifier, "schedule_incident_text", lambda *a, **k: None)

    async def _durable():
        async with app.state.session_factory() as db:
            paged = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, seeded.incident_id
            )
        return (
            [p.user_id for p in paged if p.step_index == 1],
            await _audit_statuses(app, seeded.incident_id),
        )

    outbound.probe = _durable
    resp = await _tap(client, ACTION_ESCALATE, seeded.incident_id)

    assert resp.status_code == 200
    assert resp.content == b""
    assert outbound.to("hooks.slack.com")[0][2]["text"] == (
        "escalated incident *Checkout 500s*."
    )
    [(paged, audits)] = outbound.probed
    assert paged and set(paged) == {bea_id}
    assert "native_action_applied" in audits


async def test_slack_refused_tap_is_committed_before_the_answer(
    client, app, outbound, card_tasks
):
    """A refusal before the action runs is audited without a commit of its
    own; the answer waits for it."""
    seeded = await _seed_slack(app)
    async with app.state.session_factory() as db:
        connector = await db.get(BotConnector, seeded.connector_id)
        connector.native_actions_enabled = False
        await db.commit()

    async def _durable():
        return await _audit_statuses(app, seeded.incident_id)

    outbound.probe = _durable
    resp = await _tap(client, ACTION_ACK, seeded.incident_id)

    assert resp.status_code == 200
    assert resp.content == b""
    assert outbound.to("hooks.slack.com")[0][2]["text"] == (
        "Native actions are disabled for this channel."
    )
    assert outbound.probed == [["native_action_refused"]]
    assert await _owner(app, seeded.incident_id) is None
    assert outbound.to("chat.update") == []


@pytest.mark.parametrize(
    ("case", "answer"),
    [
        pytest.param(
            "no_incident",
            "Could not identify the incident from that action.",
            id="no_incident",
        ),
        pytest.param(
            "no_user",
            "Slack didn't tell us who clicked the button.",
            id="no_user",
        ),
        pytest.param(
            "unlinked",
            "Your Slack account isn't linked to OpsMender. "
            "Ask an admin to add an identity link, then try again.",
            id="unlinked",
        ),
        pytest.param(
            "viewer",
            "Your OpsMender role cannot perform this action.",
            id="viewer",
        ),
    ],
)
async def test_slack_rejection_is_answered_through_response_url(
    case, answer, client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    incident_ref = "not-an-incident" if case == "no_incident" else seeded.incident_id
    user = {
        "no_incident": None,
        "no_user": {},
        "unlinked": {"id": "U_NOBODY", "name": "nobody"},
        "viewer": {"id": "U_VIEW", "name": "vic"},
    }[case]
    if case == "viewer":
        async with app.state.session_factory() as db:
            viewer = await UserRepo.create(
                db,
                username="vic",
                email="vic@slack.example",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            await UserRepo.add_to_organization(
                db, user_id=viewer.id, org_id=TEST_ORG_ID, role="viewer"
            )
            await BotUserLinkRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=seeded.connector_id,
                platform_user_id="U_VIEW",
                opsmender_user_id=viewer.id,
            )
            await db.commit()

    async def _durable():
        return await _audit_statuses(app, seeded.incident_id)

    outbound.probe = _durable
    resp = await _tap(client, ACTION_ACK, incident_ref, user=user)

    # Slack ignores the body; only the response_url answer reaches the person.
    assert resp.status_code == 200
    assert resp.content == b""
    answers = outbound.to("hooks.slack.com")
    assert [(method, url) for method, url, _ in answers] == [("POST", RESPONSE_URL)]
    assert answers[0][2] == {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": answer,
    }
    if case in {"unlinked", "viewer"}:
        assert "native_action_rejected" in outbound.probed[0]
    assert await _owner(app, seeded.incident_id) is None
    assert outbound.to("chat.update") == []


async def test_slack_card_refresh_reads_the_committed_acknowledgement(
    client, app, outbound, card_tasks, monkeypatch
):
    """The refresh commits before it schedules the delivery, which reads in a
    session of its own. Run that delivery while the request is still open, so
    the card cannot lean on the request's own commit."""
    seeded = await _seed_slack(app)
    real_get_by_id = IncidentRepo.get_by_id
    scheduled_before_return: list[int] = []

    class _DeliverFirst:
        @staticmethod
        async def get_by_id(*args, **kwargs):
            scheduled_before_return.append(len(card_tasks))
            await asyncio.gather(*card_tasks, return_exceptions=True)
            return await real_get_by_id(*args, **kwargs)

    monkeypatch.setattr("backend.api.routes.slack_paging.IncidentRepo", _DeliverFirst)
    resp = await _tap(client, ACTION_ACK, seeded.incident_id)

    assert resp.status_code == 200
    assert scheduled_before_return == [1]
    edits = outbound.to("chat.update")
    assert len(edits) == 1
    blocks = edits[0][2]["blocks"]
    assert _context(blocks).startswith("Acknowledged by Sam Ops · ")
    assert "*Acknowledged by*\nSam Ops" in _fields(blocks)
    assert ACTION_ACK not in _action_ids(blocks)


async def test_slack_resolve_edits_the_card_not_a_later_session_message(
    client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    session_ts = "1700000050.000300"
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(
            db,
            TEST_ORG_ID,
            tier=2,
            incident_id=seeded.incident_id,
            status="completed",
        )
        # The AI session's summary, posted after the card in the same channel.
        await IncidentNotificationReceiptRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=seeded.incident_id,
            connector_id=seeded.connector_id,
            platform="slack",
            lifecycle_event="session.completed",
            external_channel_id=SLACK_CHANNEL,
            external_message_id=session_ts,
            rendered_status="completed",
            can_update=True,
            session_id=session.id,
        )
        await db.commit()

    await _tap(client, ACTION_RESOLVE, seeded.incident_id)
    await _drain(card_tasks)

    edits = outbound.to("chat.update")
    assert [edit[2]["ts"] for edit in edits] == [SLACK_TS]
    assert _action_ids(edits[0][2]["blocks"]) == [ACTION_VIEW]
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.resolved"
    assert receipts[-1].external_message_id == SLACK_TS


async def test_slack_start_ai_session_leaves_the_card_alone(
    client, app, outbound, card_tasks, monkeypatch
):
    """Starting a session changes nothing the card shows, and the web emits no
    incident event for it: no card edit and no fresh card anywhere."""
    seeded = await _seed_slack(app)
    await _add_teams_channel(app, monkeypatch)
    dispatched: list[uuid.UUID] = []

    async def _dispatch(app_, session_id):
        dispatched.append(session_id)

    monkeypatch.setattr(
        "backend.services.session_orchestration.dispatch_session_ready", _dispatch
    )
    # Only the owner starts the incident's AI session.
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    assert len(outbound.to("chat.update")) == 1
    assert _teams_posts(outbound) == 1

    resp = await _tap(
        client,
        ACTION_START_AI_SESSION,
        seeded.incident_id,
        action_ts="1700000002.000002",
    )
    await _drain(card_tasks)

    assert resp.status_code == 200
    assert outbound.to("hooks.slack.com")[-1][2]["text"] == (
        "started an AI session for incident *Checkout 500s*."
    )
    assert len(dispatched) == 1
    assert len(outbound.to("chat.update")) == 1
    assert outbound.to("chat.postMessage") == []
    assert _teams_posts(outbound) == 1
    receipts = await _receipts(app, seeded.incident_id)
    assert "incident.updated" not in [r.lifecycle_event for r in receipts]


async def test_taps_that_change_nothing_leave_the_card_alone(
    client, app, outbound, card_tasks, monkeypatch
):
    """A second, distinct Acknowledge or Resolve tap changes nothing: it is
    answered, but no card is edited and Teams gets no fresh card."""
    seeded = await _seed_slack(app)
    await _add_teams_channel(app, monkeypatch)
    steps = [
        (ACTION_ACK, "acknowledged"),
        (ACTION_ACK, "already acknowledged"),
        (ACTION_RESOLVE, "resolved"),
        (ACTION_RESOLVE, "already resolved"),
    ]
    seen = []
    for index, (action_id, verb) in enumerate(steps):
        resp = await _tap(
            client, action_id, seeded.incident_id, action_ts=f"170000001{index}.000001"
        )
        await _drain(card_tasks)
        assert resp.status_code == 200
        assert outbound.to("hooks.slack.com")[-1][2]["text"] == (
            f"{verb} incident *Checkout 500s*."
        )
        seen.append((len(outbound.to("chat.update")), _teams_posts(outbound)))

    assert seen == [(1, 1), (1, 1), (2, 2), (2, 2)]
    events = [r.lifecycle_event for r in await _receipts(app, seeded.incident_id)]
    assert events.count("incident.acknowledged") == 2
    assert events.count("incident.resolved") == 2


# ---------------------------------------------------------------------------
# Slack commands update the card like a tap
# ---------------------------------------------------------------------------

HELD_ACTIONS = [ACTION_RESOLVE, ACTION_ESCALATE, ACTION_START_AI_SESSION, ACTION_VIEW]
OPEN_ACTIONS = [ACTION_ACK, *HELD_ACTIONS]


async def _command(client, command: str, incident_id) -> str:
    body, headers = _slack_command(command, str(incident_id))
    resp = await client.post("/bot/slack/commands", content=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["text"]


@pytest.mark.parametrize(
    ("command", "reply", "event", "actions"),
    [
        pytest.param(
            "/ack",
            "You recorded *Checkout 500s*.",
            "incident.acknowledged",
            HELD_ACTIONS,
            id="ack",
        ),
        pytest.param(
            "/take",
            "You're now assigned to *Checkout 500s*.",
            "incident.updated",
            HELD_ACTIONS,
            id="take",
        ),
        pytest.param(
            "/resolve",
            "Marked *Checkout 500s* resolved.",
            "incident.resolved",
            [ACTION_VIEW],
            id="resolve",
        ),
    ],
)
async def test_slack_command_edits_the_card_in_place(
    command, reply, event, actions, client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)

    assert await _command(client, command, seeded.incident_id) == reply
    await _drain(card_tasks)

    edits = outbound.to("chat.update")
    assert [edit[2]["ts"] for edit in edits] == [SLACK_TS]
    blocks = edits[0][2]["blocks"]
    assert _action_ids(blocks) == actions
    if command == "/resolve":
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, seeded.incident_id)
        assert incident.status == "resolved"
    else:
        assert await _owner(app, seeded.incident_id) == seeded.user_id
        assert _context(blocks) == (
            f"Acknowledged by Sam Ops · Incident `{seeded.incident_id}`"
        )
        assert "*Responder*\nAssigned to Sam Ops" in _fields(blocks)
    assert outbound.to("chat.postMessage") == []
    receipts = await _receipts(app, seeded.incident_id)
    assert [(r.lifecycle_event, r.delivery_status) for r in receipts[1:]] == [
        (event, "updated")
    ]

    # The same command again changes nothing, so the card is left alone.
    await _command(client, command, seeded.incident_id)
    await _drain(card_tasks)
    assert len(outbound.to("chat.update")) == 1


async def test_slack_release_command_refresh_reads_the_committed_release(
    client, app, outbound, card_tasks, monkeypatch
):
    """The command commits before it schedules the card edit, which reads in
    a session of its own. Run that delivery while the command is still open,
    so the card cannot lean on the request's own commit."""
    from backend.api.routes import incidents as incidents_routes

    seeded = await _seed_slack(app)
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    real_notify = incidents_routes._notify_channels
    delivered_inside: list[int] = []

    async def _notify_then_deliver(*args, **kwargs):
        await real_notify(*args, **kwargs)
        delivered_inside.append(len(card_tasks))
        await asyncio.gather(*card_tasks)

    monkeypatch.setattr(incidents_routes, "_notify_channels", _notify_then_deliver)

    assert await _command(client, "/release", seeded.incident_id) == (
        "Released *Checkout 500s*."
    )

    assert delivered_inside == [2]
    edits = outbound.to("chat.update")
    assert len(edits) == 2
    blocks = edits[-1][2]["blocks"]
    assert _context(blocks) == f"Incident `{seeded.incident_id}`"
    assert "*Responder*\nUnassigned" in _fields(blocks)
    assert _action_ids(blocks) == OPEN_ACTIONS
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.updated"


# ---------------------------------------------------------------------------
# Ownership changes edit the card in place
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("via", ["web", "slack_command"])
async def test_release_edits_the_card_back_to_unowned(
    via, client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    await _add_teams_channel(app, monkeypatch)
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    assert await _owner(app, seeded.incident_id) == seeded.user_id
    assert _teams_posts(outbound) == 1
    # A Slack channel added after the card went out has nothing to edit.
    async with app.state.session_factory() as db:
        await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="slack-late",
            platform="slack",
            config={"default_chat_id": "C_LATE"},
            credentials={"bot_token": "xoxb-late"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()
    pages_before = await _page_count(app, seeded.incident_id)

    if via == "web":
        resp = await client.post(
            f"/incidents/{seeded.incident_id}/release",
            headers=await _login(client, app, seeded.user_id),
        )
        assert resp.status_code == 204, resp.text
    else:
        body, headers = _slack_command("/release", str(seeded.incident_id))
        resp = await client.post("/bot/slack/commands", content=body, headers=headers)
        assert resp.json()["text"] == "Released *Checkout 500s*."
    await _drain(card_tasks)

    # Nobody holds it now: the card says so and offers Acknowledge again.
    assert await _owner(app, seeded.incident_id) is None
    edits = outbound.to("chat.update")
    assert len(edits) == 2
    assert edits[-1][2]["ts"] == SLACK_TS
    blocks = edits[-1][2]["blocks"]
    assert _context(blocks) == f"Incident `{seeded.incident_id}`"
    assert "*Responder*\nUnassigned" in _fields(blocks)
    assert not [f for f in _fields(blocks) if f.startswith("*Acknowledged by*")]
    assert _action_ids(blocks) == [
        ACTION_ACK,
        ACTION_RESOLVE,
        ACTION_ESCALATE,
        ACTION_START_AI_SESSION,
        ACTION_VIEW,
    ]
    # Edit in place only: no new post anywhere, nobody paged.
    assert outbound.to("chat.postMessage") == []
    assert _teams_posts(outbound) == 1
    assert await _page_count(app, seeded.incident_id) == pages_before
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.updated"
    assert receipts[-1].delivery_status == "updated"


async def test_force_take_edits_the_card_to_name_the_new_owner(
    client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    await _add_teams_channel(app, monkeypatch)
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    async with app.state.session_factory() as db:
        ada = await UserRepo.create(
            db,
            username="ada",
            email="ada@slack.example",
            password_hash="x",
            role="admin",
            primary_org_id=TEST_ORG_ID,
            first_name="Ada",
            last_name="Min",
        )
        await UserRepo.add_to_organization(
            db, user_id=ada.id, org_id=TEST_ORG_ID, role="admin"
        )
        await db.commit()

    resp = await client.post(
        f"/incidents/{seeded.incident_id}/take",
        json={"force": True, "reason": "Sam is offline"},
        headers=await _login(client, app, ada.id),
    )
    assert resp.status_code == 200, resp.text
    await _drain(card_tasks)

    assert await _owner(app, seeded.incident_id) == ada.id
    edits = outbound.to("chat.update")
    assert len(edits) == 2
    blocks = edits[-1][2]["blocks"]
    assert _context(blocks).startswith("Acknowledged by Ada Min · ")
    assert "*Responder*\nAssigned to Ada Min" in _fields(blocks)
    assert ACTION_ACK not in _action_ids(blocks)
    assert _teams_posts(outbound) == 1


async def _add_user(
    app, username: str, first_name: str, *, role="operator", team_id=None
) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=username,
            email=f"{username}@cards.example",
            password_hash="x",
            role=role,
            primary_org_id=TEST_ORG_ID,
            first_name=first_name,
            last_name="Two",
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role=role
        )
        if team_id is not None:
            await TeamRepo.add_member(db, TEST_ORG_ID, team_id, user_id=user.id)
        await db.commit()
        return user.id


def _assert_held_by(outbound, incident_id, name: str, *, edits: int) -> None:
    """The card in the channel was edited in place, ``edits`` times in all,
    and now names ``name`` as the owner without an Acknowledge button."""
    updates = outbound.to("chat.update")
    assert [update[2]["ts"] for update in updates] == [SLACK_TS] * edits
    blocks = updates[-1][2]["blocks"]
    assert _context(blocks) == f"Acknowledged by {name} · Incident `{incident_id}`"
    assert f"*Responder*\nAssigned to {name}" in _fields(blocks)
    assert _action_ids(blocks) == HELD_ACTIONS
    assert outbound.to("chat.postMessage") == []


def _assert_unowned(outbound, incident_id, *, edits: int) -> list[dict]:
    """The card was edited in place and offers Acknowledge again."""
    updates = outbound.to("chat.update")
    assert [update[2]["ts"] for update in updates] == [SLACK_TS] * edits
    blocks = updates[-1][2]["blocks"]
    assert _context(blocks) == f"Incident `{incident_id}`"
    assert not [f for f in _fields(blocks) if f.startswith("*Acknowledged by*")]
    assert _action_ids(blocks) == OPEN_ACTIONS
    assert outbound.to("chat.postMessage") == []
    return blocks


@pytest.mark.parametrize("route", ["take", "assign"])
async def test_web_take_or_assign_edits_the_card(
    route, client, app, outbound, card_tasks, monkeypatch
):
    seeded = await _seed_slack(app)
    await _add_teams_channel(app, monkeypatch)

    resp = await client.post(
        f"/incidents/{seeded.incident_id}/{route}",
        json={},
        headers=await _login(client, app, seeded.user_id),
    )
    assert resp.status_code in (200, 201), resp.text
    await _drain(card_tasks)

    assert await _owner(app, seeded.incident_id) == seeded.user_id
    _assert_held_by(outbound, seeded.incident_id, "Sam Ops", edits=1)
    # Edit in place only: Teams cannot edit, so it gets nothing.
    assert _teams_posts(outbound) == 0
    receipts = await _receipts(app, seeded.incident_id)
    assert (receipts[-1].lifecycle_event, receipts[-1].delivery_status) == (
        "incident.updated",
        "updated",
    )


async def test_confirmed_takeover_edits_the_card_to_name_the_taker(
    client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    bea_id = await _seed_chain(app, seeded)
    async with app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, seeded.team_id, user_id=bea_id)
        await db.commit()
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)

    asked = await client.post(
        f"/incidents/{seeded.incident_id}/take",
        json={},
        headers=await _login(client, app, bea_id),
    )
    assert asked.status_code == 200, asked.text
    await _drain(card_tasks)
    # A pending request changes no owner, so the card stays as it is.
    assert len(outbound.to("chat.update")) == 1

    confirmed = await client.post(
        f"/incidents/{seeded.incident_id}/take",
        json={"confirm": True},
        headers=await _login(client, app, seeded.user_id),
    )
    assert confirmed.status_code == 200, confirmed.text
    await _drain(card_tasks)

    assert await _owner(app, seeded.incident_id) == bea_id
    _assert_held_by(outbound, seeded.incident_id, "Bea Two", edits=2)


async def test_phone_acknowledge_edits_the_card(client, app, outbound, card_tasks):
    from backend.api.routes.voice import encode_voice_ack_token

    seeded = await _seed_slack(app)
    token = encode_voice_ack_token(
        org_id=TEST_ORG_ID, incident_id=seeded.incident_id, user_id=seeded.user_id
    )

    resp = await client.post(f"/paging/voice/ack/{token}", data={"Digits": "1"})
    await _drain(card_tasks)

    assert "Incident acknowledged. You are now the owner." in resp.text
    assert await _owner(app, seeded.incident_id) == seeded.user_id
    _assert_held_by(outbound, seeded.incident_id, "Sam Ops", edits=1)
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.acknowledged"

    # Pressing 1 again changes nothing, so the card is left alone.
    again = await client.post(f"/paging/voice/ack/{token}", data={"Digits": "1"})
    await _drain(card_tasks)
    assert "You already own this incident." in again.text
    assert len(outbound.to("chat.update")) == 1


async def test_bulk_acknowledge_and_reassign_edit_the_card(
    client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    bea_id = await _add_user(app, "bea", "Bea", team_id=seeded.team_id)
    ada_id = await _add_user(app, "ada", "Ada", role="admin")

    acked = await client.post(
        "/incidents/bulk",
        json={"action": "acknowledge", "incident_ids": [str(seeded.incident_id)]},
        headers=await _login(client, app, seeded.user_id),
    )
    assert acked.status_code == 200, acked.text
    assert acked.json()["succeeded"] == 1
    await _drain(card_tasks)
    assert await _owner(app, seeded.incident_id) == seeded.user_id
    _assert_held_by(outbound, seeded.incident_id, "Sam Ops", edits=1)

    moved = await client.post(
        "/incidents/bulk",
        json={
            "action": "reassign",
            "incident_ids": [str(seeded.incident_id)],
            "user_id": str(bea_id),
            "note": "Bea takes it",
        },
        headers=await _login(client, app, ada_id),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["succeeded"] == 1
    await _drain(card_tasks)
    assert await _owner(app, seeded.incident_id) == bea_id
    _assert_held_by(outbound, seeded.incident_id, "Bea Two", edits=2)
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.updated"


async def test_session_override_edits_the_card_once_it_takes_the_incident(
    client, app, outbound, card_tasks, monkeypatch
):
    """An admin's override makes an unowned incident theirs; overriding again
    as its owner changes no owner and leaves the card alone."""
    monkeypatch.setattr(
        "backend.api.routes.sessions.schedule_session_workflow", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "backend.api.routes.sessions.cancel_session_workflow", lambda *a, **k: None
    )
    seeded = await _seed_slack(app)
    ada_id = await _add_user(app, "ada", "Ada", role="admin")
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(
            db,
            TEST_ORG_ID,
            tier=0,
            incident_id=seeded.incident_id,
            status="active",
        )
        await db.commit()
    headers = await _login(client, app, ada_id)

    resp = await client.post(
        f"/sessions/{session.id}/override", json={"tier": 1}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    await _drain(card_tasks)
    assert await _owner(app, seeded.incident_id) == ada_id
    _assert_held_by(outbound, seeded.incident_id, "Ada Two", edits=1)

    again = await client.post(
        f"/sessions/{session.id}/override", json={"tier": 2}, headers=headers
    )
    assert again.status_code == 200, again.text
    await _drain(card_tasks)
    assert len(outbound.to("chat.update")) == 1


@pytest.mark.parametrize("change", ["service_move", "reopen", "bulk_reopen"])
async def test_service_move_or_reopen_edits_the_card_back_to_unowned(
    change, client, app, outbound, card_tasks
):
    seeded = await _seed_slack(app)
    headers = await _login(client, app, seeded.user_id)
    # Sam holds the incident, or resolved it, from the card.
    await _tap(
        client,
        ACTION_ACK if change == "service_move" else ACTION_RESOLVE,
        seeded.incident_id,
    )
    await _drain(card_tasks)
    assert ACTION_ACK not in _action_ids(outbound.to("chat.update")[0][2]["blocks"])

    if change == "service_move":
        async with app.state.session_factory() as db:
            ledger = await ServiceRepo.create(
                db, TEST_ORG_ID, team_id=seeded.team_id, name="Ledger", slug="ledger"
            )
            await db.commit()
        resp = await client.patch(
            f"/incidents/{seeded.incident_id}",
            json={
                "service_id": str(ledger.id),
                "service_id_set": True,
                "handoff_reason": "Ledger owns this",
            },
            headers=headers,
        )
    elif change == "reopen":
        resp = await client.patch(
            f"/incidents/{seeded.incident_id}", json={"status": "open"}, headers=headers
        )
    else:
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "reopen", "incident_ids": [str(seeded.incident_id)]},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    await _drain(card_tasks)

    assert await _owner(app, seeded.incident_id) is None
    blocks = _assert_unowned(outbound, seeded.incident_id, edits=2)
    if change == "service_move":
        assert "*Service*\nLedger" in _fields(blocks)
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.updated"


async def _scope_to_payments_and_add_platform(app, seeded):
    """Scope the seeded channel to Payments and add Platform, with a service
    and an operator of its own."""
    async with app.state.session_factory() as db:
        connector = await db.get(BotConnector, seeded.connector_id)
        connector.config = {
            **connector.config,
            "team_scope": "teams",
            "team_ids": [str(seeded.team_id)],
        }
        platform = await TeamRepo.create(
            db, TEST_ORG_ID, name="Platform", slug="platform"
        )
        gateway = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=platform.id, name="Edge gateway", slug="edge"
        )
        await db.commit()
    riley_id = await _add_user(app, "riley", "Riley", team_id=platform.id)
    return platform, gateway, riley_id


async def _move_to_platform(client, route, incident_id, platform, gateway, headers):
    if route == "reassign":
        return await client.post(
            f"/incidents/{incident_id}/reassign",
            json={"team_id": str(platform.id), "note": "Platform owns this"},
            headers=headers,
        )
    if route == "bulk_handoff":
        return await client.post(
            "/incidents/bulk",
            json={
                "action": "handoff",
                "incident_ids": [str(incident_id)],
                "team_id": str(platform.id),
                "note": "Platform owns this",
            },
            headers=headers,
        )
    return await client.patch(
        f"/incidents/{incident_id}",
        json={
            "service_id": str(gateway.id),
            "service_id_set": True,
            "handoff_reason": "Platform owns this",
        },
        headers=headers,
    )


@pytest.mark.parametrize("route", ["reassign", "bulk_handoff", "service_move"])
async def test_move_to_another_team_edits_the_previous_teams_card_once(
    route, client, app, outbound, card_tasks
):
    """The card went to a channel scoped to Payments. When the incident moves
    to Platform, that card gets one last edit: it says the incident moved and
    names no one (not Sam, who no longer holds it, and nothing of
    Platform's), with no buttons. Platform's later changes never reach it."""
    seeded = await _seed_slack(app)
    platform, gateway, riley_id = await _scope_to_payments_and_add_platform(app, seeded)
    await _tap(client, ACTION_ACK, seeded.incident_id)
    await _drain(card_tasks)
    assert len(outbound.to("chat.update")) == 1

    resp = await _move_to_platform(
        client,
        route,
        seeded.incident_id,
        platform,
        gateway,
        await _login(client, app, seeded.user_id),
    )
    assert resp.status_code == 200, resp.text
    await _drain(card_tasks)

    assert await _owner(app, seeded.incident_id) is None
    updates = outbound.to("chat.update")
    assert [update[2]["ts"] for update in updates] == [SLACK_TS] * 2
    edit = updates[-1][2]
    assert _fields(edit["blocks"]) == [
        "*Service*\nmanual",
        "*Team*\nMoved to another team",
        "*Org*\nChat Org",
    ]
    assert _context(edit["blocks"]) == f"Incident `{seeded.incident_id}`"
    assert _action_ids(edit["blocks"]) == [ACTION_VIEW]
    for name in ("Sam", "Platform", "Checkout API", "Edge gateway"):
        assert name not in json.dumps(edit)
    assert edit["text"].endswith(f"\n{SIGN_IN_TO_ACT_HINT}")
    assert outbound.to("chat.postMessage") == []

    # Platform takes it and lets it go: its channel cards change, the
    # Payments card does not.
    riley = await _login(client, app, riley_id)
    taken = await client.post(
        f"/incidents/{seeded.incident_id}/assign", json={}, headers=riley
    )
    assert taken.status_code in (200, 201), taken.text
    await _drain(card_tasks)
    assert await _owner(app, seeded.incident_id) == riley_id
    released = await client.post(
        f"/incidents/{seeded.incident_id}/release", headers=riley
    )
    assert released.status_code == 204, released.text
    await _drain(card_tasks)
    assert len(outbound.to("chat.update")) == 2
    assert outbound.to("chat.postMessage") == []


async def test_move_to_another_team_clears_the_discord_cards_buttons(
    client, app, outbound, card_tasks
):
    """Discord keeps whatever an edit leaves out, so the last edit of the
    previous team's card removes its buttons and facts outright."""
    seeded = await _seed(
        app,
        platform="discord",
        credentials={"public_key": "00" * 32, "bot_token": "discord-token"},
        config={"default_chat_id": DISCORD_CHANNEL},
        external_user_id="discord-dee",
        first_name="Dee",
        prior_channel=DISCORD_CHANNEL,
        prior_message=DISCORD_MESSAGE,
        can_update=True,
    )
    platform, gateway, _ = await _scope_to_payments_and_add_platform(app, seeded)

    resp = await _move_to_platform(
        client,
        "reassign",
        seeded.incident_id,
        platform,
        gateway,
        await _login(client, app, seeded.user_id),
    )
    assert resp.status_code == 200, resp.text
    await _drain(card_tasks)

    edits = [item for item in outbound.to("discord.com") if item[0] == "PATCH"]
    assert [url.rsplit("/", 1)[1] for _, url, _ in edits] == [DISCORD_MESSAGE]
    message = edits[0][2]
    assert message["embeds"] == []
    assert message["components"] == []
    assert "Team: Moved to another team" in message["content"]
    assert "Responder" not in message["content"]
    assert "Platform" not in message["content"]
    assert [item for item in outbound.to("discord.com") if item[0] == "POST"] == []


# ---------------------------------------------------------------------------
# Escalation posts
# ---------------------------------------------------------------------------


async def test_escalation_post_drops_the_sign_in_hint_only_beside_buttons(
    app, outbound
):
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout 500s",
            description="Spike in 500s",
            severity="high",
            priority="P1",
            response_mode="page",
        )
        for chat_id, native in (("discord-native", True), ("discord-plain", False)):
            connector = await BotConnectorRepo.create(
                db,
                TEST_ORG_ID,
                name=chat_id,
                platform="discord",
                config={"default_chat_id": chat_id},
                credentials={"public_key": "00" * 32, "bot_token": "discord-token"},
                allowed_capabilities=["notifications"],
                status="configured",
                is_enabled=True,
                native_actions_enabled=native,
            )
            connector.callback_status = "verified"
        await db.commit()
    # The escalation engine builds one text for every channel.
    text = build_incident_message(
        incident,
        event_type="incident.escalated",
        base_url=PUBLIC_URL,
        supports_actions=False,
    )
    assert text.endswith(f"\n{SIGN_IN_TO_ACT_HINT}")

    await notifier.deliver_incident_text(
        app.state.session_factory,
        org_id=TEST_ORG_ID,
        text=text,
        event_type="incident.escalated",
        incident_id=incident.id,
        rendered_status="open",
    )

    posts = {
        url.split("/channels/")[1].split("/")[0]: body
        for method, url, body in outbound.to("discord.com")
        if method == "POST"
    }
    assert set(posts) == {"discord-native", "discord-plain"}
    assert SIGN_IN_TO_ACT_HINT not in posts["discord-native"]["content"]
    assert posts["discord-native"]["content"].endswith(
        f"/dashboard/incidents/detail?id={incident.id}"
    )
    assert [
        b["custom_id"] for b in posts["discord-native"]["components"][0]["components"]
    ] == [
        "opsmender:ack",
        "opsmender:resolve",
        "opsmender:escalate",
        "opsmender:start_ai_session",
    ]
    assert posts["discord-plain"]["content"].endswith(f"\n{SIGN_IN_TO_ACT_HINT}")
    assert "components" not in posts["discord-plain"]


async def test_escalation_post_keeps_the_sign_in_hint_when_the_card_cannot_render(
    app, outbound
):
    """A new incident whose Response Mode escalates at once is escalated
    before the transaction that creates it commits, so the delivery's own
    session cannot read it. That post is plain text without buttons, so it
    keeps the line that says where to act."""
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="discord-native",
            platform="discord",
            config={"default_chat_id": "discord-native"},
            credentials={"public_key": "00" * 32, "bot_token": "discord-token"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        connector.callback_status = "verified"
        await db.commit()
    # Built in the escalating transaction; never committed.
    incident = SimpleNamespace(
        id=uuid.uuid4(),
        title="Checkout 500s",
        severity="critical",
        status="open",
        priority="P0",
        description="",
        external_source=None,
        created_at=None,
    )
    text = build_incident_message(
        incident,
        event_type="incident.escalated",
        base_url=PUBLIC_URL,
        supports_actions=False,
    )

    await notifier.deliver_incident_text(
        app.state.session_factory,
        org_id=TEST_ORG_ID,
        text=text,
        event_type="incident.escalated",
        incident_id=incident.id,
        rendered_status="open",
    )

    posts = [body for method, _, body in outbound.to("discord.com") if method == "POST"]
    assert len(posts) == 1
    assert "components" not in posts[0]
    assert posts[0]["content"].endswith(f"\n{SIGN_IN_TO_ACT_HINT}")


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


async def test_discord_new_card_carries_the_facts_and_drops_acknowledge(app, outbound):
    """When the earlier message cannot be edited, the card is posted again.
    The new post carries the same embed fields an edit would, and leaves
    Acknowledge off once someone holds the incident."""
    seeded = await _seed(
        app,
        platform="discord",
        credentials={"public_key": "00" * 32, "bot_token": "discord-token"},
        config={"default_chat_id": DISCORD_CHANNEL},
        external_user_id="discord-dee",
        first_name="Dee",
        prior_channel=DISCORD_CHANNEL,
        prior_message=DISCORD_MESSAGE,
        can_update=False,
    )
    async with app.state.session_factory() as db:
        await _esc.acknowledge(
            db,
            TEST_ORG_ID,
            incident_id=seeded.incident_id,
            assignee_id=seeded.user_id,
        )
        await db.commit()

    await notifier.deliver_incident_event(
        app.state.session_factory,
        org_id=TEST_ORG_ID,
        incident_id=seeded.incident_id,
        event_type="incident.acknowledged",
    )

    calls = outbound.to(f"/channels/{DISCORD_CHANNEL}/messages")
    assert [method for method, _, _ in calls] == ["POST"]
    message = calls[0][2]
    assert message["embeds"][0]["fields"] == [
        {"name": label, "value": value, "inline": True}
        for label, value in (
            ("Service", "Checkout API"),
            ("Team", "Payments"),
            ("Org", "Chat Org"),
            ("Responder", "Assigned to Dee Ops"),
            ("Acknowledged by", "Dee Ops"),
        )
    ]
    assert message["embeds"][0]["footer"]["text"].endswith(str(seeded.incident_id))
    assert [b["custom_id"] for b in message["components"][0]["components"]] == [
        "opsmender:resolve",
        "opsmender:escalate",
        "opsmender:start_ai_session",
    ]
    receipts = await _receipts(app, seeded.incident_id)
    assert receipts[-1].lifecycle_event == "incident.acknowledged"
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
