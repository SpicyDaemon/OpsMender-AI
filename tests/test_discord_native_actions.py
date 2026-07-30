"""Discord message components backed by verified native incident actions."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.bots.connectors import get_adapter
from backend.bots.connectors.discord import build_discord_incident_payload
from backend.config_loader import set_env_path
from backend.db.models import Base, BotActionAudit, NativeActionInvocation, Organization
from backend.db.repos import (
    BotConnectorRepo,
    BotUserLinkRepo,
    IncidentPageRepo,
    IncidentRepo,
    UserRepo,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000ddd")


@pytest.fixture
async def app(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'discord-actions.db'}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Organization(id=TEST_ORG_ID, name="Discord Org", slug="discord-org")
        )
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
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http


@pytest.fixture
def signing_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def followups(monkeypatch):
    messages: list[str] = []
    adapter = get_adapter("discord")

    async def _capture_followup(*, application_id, interaction_token, text):
        assert application_id == "discord-app"
        assert interaction_token
        messages.append(text)
        return True, None

    monkeypatch.setattr(adapter, "update_interaction_response", _capture_followup)
    return messages


async def _seed_connector(app, signing_key):
    public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="discord-native-actions",
            platform="discord",
            credentials={
                "public_key": public_key.hex(),
                "bot_token": "discord-bot-token",
            },
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        connector.callback_status = "configured"
        await db.commit()
        return connector


async def _seed_incident(app):
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="database latency",
            description="writes are slow",
            severity="high",
            priority="P1",
            response_mode="page",
        )
        await db.commit()
        return incident


async def _seed_linked_user(
    app,
    *,
    connector_id,
    discord_user_id,
    role="operator",
):
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=f"discord-{discord_user_id}",
            email=f"{discord_user_id}@test.example",
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
        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector_id,
            platform_user_id=discord_user_id,
            opsmender_user_id=user.id,
        )
        await db.commit()
        return user


def _component_payload(
    *,
    incident_id,
    user_id,
    interaction_id="discord-interaction-1",
    action_id="opsmender:ack",
):
    return {
        "type": 3,
        "id": interaction_id,
        "application_id": "discord-app",
        "token": f"token-{interaction_id}",
        "channel_id": "discord-channel",
        "member": {
            "user": {
                "id": user_id,
                "username": "responder",
                "global_name": "Responder",
            }
        },
        "data": {
            "component_type": 2,
            "custom_id": action_id,
        },
        "message": {
            "id": "discord-message",
            "embeds": [
                {
                    "footer": {
                        "text": f"OpsMender incident {incident_id}",
                    }
                }
            ],
        },
    }


def _signed_request(signing_key, payload):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "1720000000"
    signature = signing_key.sign(timestamp.encode() + body).hex()
    return body, {
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": timestamp,
        "Content-Type": "application/json",
    }


def test_discord_incident_payload_renders_all_four_action_ids():
    incident_id = uuid.uuid4()
    incident = type("IncidentStub", (), {"id": incident_id, "status": "open"})()
    payload = build_discord_incident_payload(
        "page",
        incident=incident,
        native_actions_ready=True,
    )

    buttons = payload["components"][0]["components"]
    assert [button["custom_id"] for button in buttons] == [
        "opsmender:ack",
        "opsmender:resolve",
        "opsmender:escalate",
        "opsmender:start_ai_session",
    ]
    assert payload["embeds"][0]["footer"]["text"].endswith(str(incident_id))


async def test_discord_component_rejects_bad_signature(
    client,
    app,
    signing_key,
    followups,
):
    connector = await _seed_connector(app, signing_key)
    incident = await _seed_incident(app)
    payload = _component_payload(incident_id=incident.id, user_id="discord-user")
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = await client.post(
        f"/bot-connectors/{connector.id}/discord/webhook",
        content=body,
        headers={
            "X-Signature-Ed25519": "00" * 64,
            "X-Signature-Timestamp": "1720000000",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    assert followups == []


async def test_discord_component_refuses_unlinked_actor(
    client,
    app,
    signing_key,
    followups,
):
    connector = await _seed_connector(app, signing_key)
    incident = await _seed_incident(app)
    body, headers = _signed_request(
        signing_key,
        _component_payload(incident_id=incident.id, user_id="discord-stranger"),
    )

    response = await client.post(
        f"/bot-connectors/{connector.id}/discord/webhook",
        content=body,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"type": 5, "data": {"flags": 64}}
    assert len(followups) == 1
    assert "Discord account isn't linked" in followups[0]


async def test_discord_component_refuses_unauthorized_role(
    client,
    app,
    signing_key,
    followups,
):
    connector = await _seed_connector(app, signing_key)
    await _seed_linked_user(
        app,
        connector_id=connector.id,
        discord_user_id="discord-viewer",
        role="viewer",
    )
    incident = await _seed_incident(app)
    body, headers = _signed_request(
        signing_key,
        _component_payload(incident_id=incident.id, user_id="discord-viewer"),
    )

    response = await client.post(
        f"/bot-connectors/{connector.id}/discord/webhook",
        content=body,
        headers=headers,
    )

    assert response.status_code == 200
    assert "role cannot perform" in followups[0]


async def test_discord_linked_operator_ack_is_exactly_once_on_replay(
    client,
    app,
    signing_key,
    followups,
):
    connector = await _seed_connector(app, signing_key)
    user = await _seed_linked_user(
        app,
        connector_id=connector.id,
        discord_user_id="discord-operator",
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

    payload = _component_payload(
        incident_id=incident.id,
        user_id="discord-operator",
        interaction_id="discord-replay-key",
    )
    body, headers = _signed_request(signing_key, payload)
    path = f"/bot-connectors/{connector.id}/discord/webhook"

    first = await client.post(path, content=body, headers=headers)
    replay = await client.post(path, content=body, headers=headers)

    assert first.json() == {"type": 5, "data": {"flags": 64}}
    assert replay.json() == {"type": 5, "data": {"flags": 64}}
    assert len(followups) == 2
    assert all("acknowledged" in message for message in followups)

    async with app.state.session_factory() as db:
        invocations = (
            (
                await db.execute(
                    select(NativeActionInvocation).where(
                        NativeActionInvocation.connector_id == connector.id,
                        NativeActionInvocation.idempotency_key == "discord-replay-key",
                    )
                )
            )
            .scalars()
            .all()
        )
        audits = (
            (
                await db.execute(
                    select(BotActionAudit).where(
                        BotActionAudit.connector_id == connector.id,
                        BotActionAudit.idempotency_key == "discord-replay-key",
                    )
                )
            )
            .scalars()
            .all()
        )
        reloaded = await BotConnectorRepo.get_by_id(
            db,
            TEST_ORG_ID,
            connector.id,
        )

    assert len(invocations) == 1
    assert invocations[0].status == "applied"
    assert [audit.status for audit in audits].count("native_action_applied") == 1
    assert [audit.status for audit in audits].count("native_action_deduplicated") == 1
    assert reloaded.callback_status == "verified"
