"""Tests for the Sprint 36 Slack page card + interactivity endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base, Incident, Organization
from backend.db.repos import (
    BotConnectorRepo,
    BotUserLinkRepo,
    IncidentPageRepo,
    IncidentRepo,
    UserRepo,
)
from backend.paging.slack_cards import (
    ACTION_ACK,
    ACTION_ESCALATE,
    ACTION_RESOLVE,
    ACTION_START_AI_SESSION,
    ACTION_VIEW,
    build_page_card_blocks,
    build_page_card_text,
    parse_incident_id_from_action,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000aaa")
SIGNING_SECRET = "test-signing-secret"


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "slack-paging.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Slack Org", slug="slack-org"))
        await session.commit()
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    class _Pool:
        async def get_server(self, *a, **kw):
            return object()

        @asynccontextmanager
        async def connect(self, *a, **kw):
            class _S:
                pass

            yield _S()

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
    ) as c:
        yield c


def _slack_sign(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    basestring = f"v0:{ts}:{body.decode('utf-8')}"
    sig = (
        "v0="
        + hmac.new(
            SIGNING_SECRET.encode("utf-8"),
            basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _seed_slack_connector(app, *, signing_secret=SIGNING_SECRET):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="slack-test",
            platform="slack",
            credentials={"signing_secret": signing_secret, "bot_token": "xoxb-t"},
            allowed_capabilities=["paging"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        await db.commit()
        return connector


async def _seed_user_and_link(
    app,
    *,
    connector_id,
    slack_user_id="U_TEST",
    role="operator",
):
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=f"sl-{slack_user_id}",
            email=f"{slack_user_id}@test.com",
            password_hash="x",
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db,
            user_id=user.id,
            org_id=TEST_ORG_ID,
            role=role,
        )
        link = await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector_id,
            platform_user_id=slack_user_id,
            opsmender_user_id=user.id,
        )
        await db.commit()
        return user, link


async def _seed_incident(app, *, title="boom") -> Incident:
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="things broke",
            severity="high",
            priority="P1",
            response_mode="page",
        )
        await db.commit()
        return incident


def _block_actions_payload(*, action_id, incident_id, user_id):
    return {
        "type": "block_actions",
        "user": {"id": user_id, "name": "alice"},
        "actions": [
            {
                "action_id": action_id,
                "block_id": f"opsmender:incident:{incident_id}:actions",
                "value": str(incident_id),
            }
        ],
    }


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------


class TestPageCardBuilder:
    def test_text_includes_priority_and_title(self):
        inc = Incident(
            id=uuid.uuid4(),
            org_id=TEST_ORG_ID,
            title="db on fire",
            priority="P0",
            status="open",
        )
        assert build_page_card_text(inc) == "[P0] OpsMender page: db on fire"

    def test_blocks_hide_actions_until_channel_is_ready(self):
        inc = Incident(
            id=uuid.uuid4(),
            org_id=TEST_ORG_ID,
            title="latency spike",
            priority="P1",
            status="open",
            severity="high",
        )
        blocks = build_page_card_blocks(inc)
        assert [b for b in blocks if b["type"] == "actions"] == []

        blocks = build_page_card_blocks(inc, include_native_actions=True)
        actions = [b for b in blocks if b["type"] == "actions"][0]
        action_ids = [e["action_id"] for e in actions["elements"]]
        assert ACTION_ACK in action_ids
        assert ACTION_RESOLVE in action_ids
        assert ACTION_ESCALATE in action_ids
        assert ACTION_START_AI_SESSION in action_ids
        # No base_url → no View button.
        assert ACTION_VIEW not in action_ids

    def test_view_button_added_when_base_url_set(self):
        inc = Incident(
            id=uuid.uuid4(),
            org_id=TEST_ORG_ID,
            title="t",
            priority="P2",
            status="open",
        )
        blocks = build_page_card_blocks(
            inc,
            base_url="https://opsmender.example.com",
            include_native_actions=True,
        )
        actions = [b for b in blocks if b["type"] == "actions"][0]
        view = [e for e in actions["elements"] if e["action_id"] == ACTION_VIEW][0]
        assert view["url"].startswith(
            "https://opsmender.example.com/dashboard/incidents/detail"
        )
        assert "from=slack" in view["url"]

    def test_parse_incident_id_from_action_uses_value_then_block_id(self):
        incident_id = uuid.uuid4()
        payload = _block_actions_payload(
            action_id=ACTION_ACK, incident_id=incident_id, user_id="U1"
        )
        assert parse_incident_id_from_action(payload) == incident_id

        # Without value, falls back to block_id parsing.
        payload["actions"][0]["value"] = None
        assert parse_incident_id_from_action(payload) == incident_id

        # Garbage actions → None.
        assert parse_incident_id_from_action({"actions": []}) is None


# ---------------------------------------------------------------------------
# Interactions endpoint
# ---------------------------------------------------------------------------


class TestSlackInteractionsEndpoint:
    async def test_rejects_invalid_signature(self, client, app):
        await _seed_slack_connector(app)
        # Send a payload without signing.
        resp = await client.post(
            "/bot/slack/interactions",
            data={"payload": json.dumps({"type": "block_actions"})},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "invalid_signature"

    async def test_rejects_stale_signature(self, client, app):
        await _seed_slack_connector(app)
        body = urllib.parse.urlencode(
            {"payload": json.dumps({"type": "block_actions"})}
        ).encode()
        timestamp = str(int(time.time()) - 601)
        signature = (
            "v0="
            + hmac.new(
                SIGNING_SECRET.encode(),
                f"v0:{timestamp}:{body.decode()}".encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        resp = await client.post(
            "/bot/slack/interactions",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": timestamp,
                "X-Slack-Signature": signature,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert resp.status_code == 403

    async def test_ack_button_acks_chain(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_ACK"
        )
        incident = await _seed_incident(app)

        # Need an open chain state so handle_ack returns True.
        async with app.state.session_factory() as db:
            await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                user_id=user.id,
            )
            await db.commit()

        payload = _block_actions_payload(
            action_id=ACTION_ACK, incident_id=incident.id, user_id="U_ACK"
        )
        # Form-encode the value of `payload` so the server can read it via
        # request.form().
        encoded = urllib.parse.urlencode({"payload": json.dumps(payload)})
        body_bytes = encoded.encode("utf-8")
        headers = _slack_sign(body_bytes)

        resp = await client.post(
            "/bot/slack/interactions",
            content=body_bytes,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert (
            "acknowledged" in resp.json()["text"] or "recorded" in resp.json()["text"]
        )
        async with app.state.session_factory() as db:
            reloaded = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
            assert reloaded.callback_status == "verified"

    async def test_unlinked_user_gets_friendly_ephemeral(self, client, app):
        await _seed_slack_connector(app)
        incident = await _seed_incident(app)
        # No BotUserLink for this Slack user id.

        payload = _block_actions_payload(
            action_id=ACTION_ACK, incident_id=incident.id, user_id="U_STRANGER"
        )
        body_bytes = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode(
            "utf-8"
        )
        headers = _slack_sign(body_bytes)
        resp = await client.post(
            "/bot/slack/interactions",
            content=body_bytes,
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["response_type"] == "ephemeral"
        assert "isn't linked" in body["text"]

    async def test_viewer_cannot_mutate_from_slack(self, client, app):
        connector = await _seed_slack_connector(app)
        await _seed_user_and_link(
            app,
            connector_id=connector.id,
            slack_user_id="U_VIEWER",
            role="viewer",
        )
        incident = await _seed_incident(app)
        payload = _block_actions_payload(
            action_id=ACTION_RESOLVE,
            incident_id=incident.id,
            user_id="U_VIEWER",
        )
        body = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()
        resp = await client.post(
            "/bot/slack/interactions",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "role cannot perform" in resp.json()["text"]
        async with app.state.session_factory() as db:
            reloaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
            assert reloaded.status != "resolved"

    async def test_view_action_is_noop_ack(self, client, app):
        await _seed_slack_connector(app)
        incident = await _seed_incident(app)
        payload = _block_actions_payload(
            action_id=ACTION_VIEW, incident_id=incident.id, user_id="U_X"
        )
        body_bytes = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode(
            "utf-8"
        )
        resp = await client.post(
            "/bot/slack/interactions",
            content=body_bytes,
            headers=_slack_sign(body_bytes),
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_resolve_button_cancels_chain_and_marks_resolved(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_RES"
        )
        incident = await _seed_incident(app, title="resolve me")
        payload = _block_actions_payload(
            action_id=ACTION_RESOLVE, incident_id=incident.id, user_id="U_RES"
        )
        body_bytes = urllib.parse.urlencode({"payload": json.dumps(payload)}).encode(
            "utf-8"
        )
        resp = await client.post(
            "/bot/slack/interactions",
            content=body_bytes,
            headers=_slack_sign(body_bytes),
        )
        assert resp.status_code == 200
        assert "resolved" in resp.json()["text"]

        async with app.state.session_factory() as db:
            reloaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
            assert reloaded.status == "resolved"


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def _slash_body(*, command: str, text: str, user_id: str) -> bytes:
    import urllib.parse

    return urllib.parse.urlencode(
        {
            "command": command,
            "text": text,
            "user_id": user_id,
            "team_id": "T1",
            "channel_id": "C1",
            "response_url": "https://hooks.slack.com/x",
        }
    ).encode("utf-8")


class TestSlackSlashCommandEndpoint:
    async def test_rejects_invalid_signature(self, client, app):
        await _seed_slack_connector(app)
        resp = await client.post(
            "/bot/slack/commands",
            data={"command": "/ack", "text": "", "user_id": "U1"},
        )
        assert resp.status_code == 403

    async def test_unknown_command_returns_ephemeral(self, client, app):
        await _seed_slack_connector(app)
        body = _slash_body(command="/nope", text="", user_id="U1")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "Unknown command" in resp.json()["text"]

    async def test_unlinked_user_friendly(self, client, app):
        await _seed_slack_connector(app)
        incident = await _seed_incident(app)
        body = _slash_body(command="/ack", text=str(incident.id), user_id="U_NOLINK")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "isn't linked" in resp.json()["text"]

    async def test_ack_with_explicit_id(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_A"
        )
        incident = await _seed_incident(app)
        async with app.state.session_factory() as db:
            await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                user_id=user.id,
            )
            await db.commit()
        body = _slash_body(command="/ack", text=str(incident.id), user_id="U_A")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        text_out = resp.json()["text"]
        assert "acknowledged" in text_out or "recorded" in text_out

    async def test_ack_falls_back_to_latest_paged_incident(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_B"
        )
        incident = await _seed_incident(app, title="auto-resolved-target")
        # Seed a chain state + page so the fallback lookup matches.
        async with app.state.session_factory() as db:
            from backend.db.repos import (
                EscalationChainRepo,
                IncidentChainStateRepo,
                TeamRepo,
            )

            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="t", slug=f"t-{uuid.uuid4().hex[:8]}"
            )
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team.id, name="c", description=None
            )
            await IncidentChainStateRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
            )
            await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                user_id=user.id,
            )
            await db.commit()
        body = _slash_body(command="/ack", text="", user_id="U_B")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "auto-resolved-target" in resp.json()["text"]

    async def test_resolve_marks_incident_resolved(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_R"
        )
        incident = await _seed_incident(app, title="bye")
        body = _slash_body(command="/resolve", text=str(incident.id), user_id="U_R")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        async with app.state.session_factory() as db:
            reloaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
            assert reloaded.status == "resolved"

    async def test_snooze_pushes_due_time_forward(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_S"
        )
        incident = await _seed_incident(app, title="snz")
        async with app.state.session_factory() as db:
            from backend.db.repos import (
                EscalationChainRepo,
                IncidentChainStateRepo,
                TeamRepo,
            )

            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="t-s", slug=f"ts-{uuid.uuid4().hex[:8]}"
            )
            chain = await EscalationChainRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="c-s",
                description=None,
            )
            state = await IncidentChainStateRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
            )
            await db.commit()
            state_id = state.id
        body = _slash_body(command="/snooze", text=f"{incident.id} 30m", user_id="U_S")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "Snoozed" in resp.json()["text"]
        async with app.state.session_factory() as db:
            from backend.db.models import IncidentChainState
            from sqlalchemy import select

            row = (
                await db.execute(
                    select(IncidentChainState).where(IncidentChainState.id == state_id)
                )
            ).scalar_one()
            assert row.next_step_due_at is not None
            assert row.status == "paused"

    async def test_snooze_rejects_bad_duration(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_SB"
        )
        incident = await _seed_incident(app)
        body = _slash_body(
            command="/snooze", text=f"{incident.id} banana", user_id="U_SB"
        )
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "Usage" in resp.json()["text"]

    async def test_status_no_args_lists_active_chains(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_ST"
        )
        # Empty state first.
        body = _slash_body(command="/status", text="", user_id="U_ST")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "No active escalation chains" in resp.json()["text"]

    async def test_release_with_no_assignment(self, client, app):
        connector = await _seed_slack_connector(app)
        user, _ = await _seed_user_and_link(
            app, connector_id=connector.id, slack_user_id="U_REL"
        )
        incident = await _seed_incident(app, title="unowned")
        body = _slash_body(command="/release", text=str(incident.id), user_id="U_REL")
        resp = await client.post(
            "/bot/slack/commands",
            content=body,
            headers=_slack_sign(body),
        )
        assert resp.status_code == 200
        assert "no active assignee" in resp.json()["text"]
