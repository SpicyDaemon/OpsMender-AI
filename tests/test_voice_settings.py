from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.auth.secrets import encrypt_secret
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import (
    AuditEntryRepo,
    IncidentPageRepo,
    IncidentRepo,
    OrgVoiceSettingsRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.paging.dispatch import dispatch_page
from backend.paging.voice_settings import resolve_voice_settings

TEST_ORG_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'voice.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Organization(id=TEST_ORG_ID, name="Voice Org", slug="voice-org"))
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def app(tmp_path, session_factory):
    set_session_factory(session_factory)
    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n",
        encoding="utf-8",
    )
    set_env_path(tmp_env)
    application = create_app()
    application.state.session_factory = session_factory
    application.dependency_overrides[get_db] = _override_get_db(session_factory)
    yield application
    set_env_path(None)
    pending = list(getattr(application.state, "session_tasks", set())) + list(
        getattr(application.state, "background_tasks", set())
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _override_get_db(factory):
    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _get_db


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_headers(client, app):
    await client.post(
        "/auth/register",
        json={
            "username": "admin",
            "email": "admin@example.test",
            "password": "securepass123",
            "role": "admin",
        },
    )
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "admin")
        if user is not None:
            user.primary_org_id = TEST_ORG_ID
            await db.commit()
    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_resolver_prefers_complete_database_settings(session_factory):
    env = {
        "OPSMENDER_TWILIO_ACCOUNT_SID": "ACENV",
        "OPSMENDER_TWILIO_AUTH_TOKEN": "env-token",
        "OPSMENDER_TWILIO_FROM_NUMBER": "+15550000000",
    }
    async with session_factory() as db:
        assert await resolve_voice_settings(db, TEST_ORG_ID, env={}) is None
        env_only = await resolve_voice_settings(db, TEST_ORG_ID, env=env)
        assert env_only is not None
        assert env_only.account_sid == "ACENV"
        assert env_only.source == "environment"

        await OrgVoiceSettingsRepo.upsert(
            db,
            TEST_ORG_ID,
            account_sid="ACDB",
            auth_token_encrypted=encrypt_secret("db-token"),
            sms_from_number="+15551111111",
            voice_from_number="+15552222222",
            enabled=True,
        )
        await db.commit()

        db_settings = await resolve_voice_settings(db, TEST_ORG_ID, env=env)
        assert db_settings is not None
        assert db_settings.account_sid == "ACDB"
        assert db_settings.auth_token == "db-token"
        assert db_settings.voice_from_number == "+15552222222"
        assert db_settings.source == "database"


async def test_voice_settings_api_masks_and_preserves_token(
    client, app, admin_headers
):
    unavailable = await client.get(
        "/api/v1/paging/channel-availability",
        headers=admin_headers,
    )
    assert unavailable.status_code == 200
    assert unavailable.json() == {"sms": False, "voice": False}

    saved = await client.put(
        "/api/v1/voice-settings",
        json={
            "enabled": True,
            "account_sid": "ACDB",
            "auth_token": "super-secret",
            "sms_from_number": "+15551111111",
            "voice_from_number": "+15552222222",
        },
        headers=admin_headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True
    assert body["auth_token_set"] is True
    assert "auth_token" not in body

    masked = await client.get("/api/v1/voice-settings", headers=admin_headers)
    assert masked.status_code == 200
    assert "auth_token" not in masked.json()

    partial = await client.put(
        "/api/v1/voice-settings",
        json={"account_sid": "ACDB2"},
        headers=admin_headers,
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["auth_token_set"] is True

    available = await client.get(
        "/api/v1/paging/channel-availability",
        headers=admin_headers,
    )
    assert available.json() == {"sms": True, "voice": True}

    async with app.state.session_factory() as db:
        resolved = await resolve_voice_settings(db, TEST_ORG_ID, env={})
        assert resolved is not None
        assert resolved.account_sid == "ACDB2"
        assert resolved.auth_token == "super-secret"
        audits = await AuditEntryRepo.query(db, TEST_ORG_ID)
        assert any(entry.entry_type == "voice_settings_update" for entry in audits)


async def test_dispatch_builds_sms_and_voice_from_database_settings(
    session_factory, monkeypatch
):
    captured: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            (
                str(request.url),
                {k: v[0] for k, v in parse_qs(request.content.decode()).items()},
            )
        )
        return httpx.Response(201, json={"sid": "OK"})

    import backend.paging.channels as channels

    monkeypatch.setattr(
        channels,
        "_default_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        ),
    )

    async with session_factory() as db:
        await OrgVoiceSettingsRepo.upsert(
            db,
            TEST_ORG_ID,
            account_sid="ACDB",
            auth_token_encrypted=encrypt_secret("db-token"),
            sms_from_number="+15551111111",
            voice_from_number="+15552222222",
            enabled=True,
        )
        user = await UserRepo.create(
            db,
            username="operator",
            email="operator@example.test",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        user.phone = "+15553333333"
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Payment path down",
            description="checkout failures",
            priority="P0",
            response_mode="page",
        )
        page = await IncidentPageRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            user_id=user.id,
        )
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            user.id,
            channels={},
            routing={"P0": ["sms", "voice"]},
        )
        await db.commit()

        result = await dispatch_page(
            db,
            TEST_ORG_ID,
            incident=incident,
            user=user,
            page=page,
            channel_factory=lambda key: None,
        )

    assert {attempt.channel: attempt.status for attempt in result.attempts} == {
        "sms": "sent",
        "voice": "sent",
    }
    assert len(captured) == 2
    sms_form = captured[0][1]
    voice_form = captured[1][1]
    assert sms_form["From"] == "+15551111111"
    assert voice_form["From"] == "+15552222222"


async def test_verify_twilio_credentials_valid():
    from backend.paging.voice_settings import (
        ResolvedVoiceSettings,
        verify_twilio_credentials,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Accounts/AC123.json" in str(request.url)
        assert request.headers.get("authorization")  # basic auth attached
        return httpx.Response(200, json={"friendly_name": "My Twilio"})

    settings = ResolvedVoiceSettings(
        account_sid="AC123",
        auth_token="tok",
        sms_from_number="+15551234567",
        voice_from_number="+15551234567",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok, message = await verify_twilio_credentials(settings, client=client)
    await client.aclose()
    assert ok is True
    assert "My Twilio" in message


async def test_verify_twilio_credentials_rejected():
    from backend.paging.voice_settings import (
        ResolvedVoiceSettings,
        verify_twilio_credentials,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "auth failed"})

    settings = ResolvedVoiceSettings(
        account_sid="AC123",
        auth_token="bad",
        sms_from_number="+15551234567",
        voice_from_number="+15551234567",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok, message = await verify_twilio_credentials(settings, client=client)
    await client.aclose()
    assert ok is False
    assert "rejected" in message.lower()
