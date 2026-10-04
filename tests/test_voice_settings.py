from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.auth.secrets import encrypt_secret
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import (
    AuditEntryRepo,
    IncidentAssignmentRepo,
    IncidentPageRepo,
    IncidentRepo,
    NotificationEscalationRepo,
    OrgVoiceSettingsRepo,
    ServiceRepo,
    TeamRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.paging import notification_escalation as ne
from backend.paging.dispatch import DeliveryAttempt, dispatch_page
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


async def test_voice_settings_api_masks_and_preserves_token(client, app, admin_headers):
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


class TestStagedVoiceDelivery:
    @pytest.mark.parametrize(
        "case",
        [
            "saved_settings",
            "later_stage",
            "environment_settings",
            "recipient_override",
            "unconfigured",
            "missing_phone",
            "no_public_url",
            "provider_rejected",
            "foreign_service",
            "immediate_control",
        ],
    )
    async def test_voice_delivery_and_keypad(
        self, case, client, app, admin_headers, session_factory, monkeypatch
    ):
        import backend.paging.channels as channels

        for key in tuple(os.environ):
            if key.startswith("OPSMENDER_TWILIO_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("OPSMENDER_PUBLIC_URL", "https://paging.example.test/")
        if case == "no_public_url":
            monkeypatch.delenv("OPSMENDER_PUBLIC_URL")
        configured = case != "unconfigured"
        if case == "environment_settings":
            monkeypatch.setenv("OPSMENDER_TWILIO_ACCOUNT_SID", "ACENV")
            monkeypatch.setenv("OPSMENDER_TWILIO_AUTH_TOKEN", "fake-env-token")
            monkeypatch.setenv("OPSMENDER_TWILIO_FROM_NUMBER", "+15551111111")
            monkeypatch.setenv("OPSMENDER_TWILIO_VOICE_FROM_NUMBER", "+15552222222")
        elif configured:
            saved = await client.put(
                "/api/v1/voice-settings",
                headers=admin_headers,
                json={
                    "enabled": True,
                    "account_sid": "ACDB",
                    "auth_token": "fake-db-token",
                    "sms_from_number": "+15551111111",
                    "voice_from_number": "+15552222222",
                },
            )
            assert saved.status_code == 200
            assert saved.json()["configured"] is True
            assert "auth_token" not in saved.json()

        available = await client.get(
            "/api/v1/paging/channel-availability", headers=admin_headers
        )
        assert available.status_code == 200
        assert available.json()["voice"] is configured

        async with session_factory() as db:
            user = await UserRepo.get_by_username(db, "admin")
            user.phone = None if case == "missing_phone" else "+15553333333"
            user_id = user.id
            service_org = TEST_ORG_ID
            if case == "foreign_service":
                service_org = uuid.uuid4()
                db.add(Organization(id=service_org, name="Private Org", slug="private"))
                await db.flush()
            team = await TeamRepo.create(
                db, service_org, name="Payments", slug="payments"
            )
            service = await ServiceRepo.create(
                db, service_org, team_id=team.id, name="Payments & API", slug="api"
            )
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Database <pool> exhausted",
                description="Connection failures",
                priority="P0",
                response_mode="page",
                service_id=service.id,
            )
            incident_id = incident.id
            page = await IncidentPageRepo.create(
                db, TEST_ORG_ID, incident_id=incident_id, user_id=user_id
            )
            await db.commit()

        stages = [
            {"channel_id": "voice", "delay_seconds": 300},
            {"channel_id": "email", "delay_seconds": 300},
        ]
        if case == "later_stage":
            stages.reverse()
        routing = {"P0": ["voice"] if case == "immediate_control" else stages}
        destinations = {"voice": "+15554444444"} if case == "recipient_override" else {}
        prefs = await client.put(
            "/users/me/notification-preferences",
            headers=admin_headers,
            json={"routing": routing, "channels": destinations},
        )
        assert prefs.status_code == 200
        assert prefs.json()["routing"] == routing

        calls = []
        email_calls = []

        def handler(request):
            calls.append(
                {
                    key: value[0]
                    for key, value in parse_qs(request.content.decode()).items()
                }
            )
            if case == "provider_rejected":
                return httpx.Response(400, json={"message": "synthetic rejection"})
            return httpx.Response(201, json={"sid": "CAFAKE", "status": "queued"})

        monkeypatch.setattr(
            channels,
            "_default_http_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        class RecordingEmail:
            async def send(self, *, recipient, subject, body):
                email_calls.append(recipient)
                return DeliveryAttempt("email", "sent")

        def factory(key):
            return RecordingEmail() if key == "email" else None

        sender = ne.build_notification_sender(factory)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id),
                user=await UserRepo.get_by_id(db, user_id),
                page=page,
                channel_factory=factory,
                at=now,
            )
            assert result.staged is (case != "immediate_control")
            await db.commit()

        if case == "later_stage":
            assert calls == [] and len(email_calls) == 1
            async with session_factory() as db:
                assert (
                    await ne.tick_all_due(
                        db, sender=sender, at=now + timedelta(seconds=299)
                    )
                    == 0
                )
                assert (
                    await ne.tick_all_due(
                        db, sender=sender, at=now + timedelta(seconds=300)
                    )
                    == 1
                )
                await db.commit()

        expected_status, expected_error = "sent", None
        if case == "unconfigured":
            expected_status, expected_error = "skipped", "channel_unconfigured"
        elif case == "missing_phone":
            expected_status, expected_error = "skipped", "no_recipient"
        elif case == "provider_rejected":
            expected_status, expected_error = "failed", "http 400: synthetic rejection"

        async with session_factory() as db:
            persisted = await UserNotificationPrefRepo.get_for_user(
                db, TEST_ORG_ID, user_id
            )
            assert persisted.routing == routing and persisted.channels == destinations
            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
            voice_pages = [row for row in pages if row.channel == "voice"]
            assert len(voice_pages) == 1
            assert voice_pages[0].delivery_status == expected_status
            assert voice_pages[0].delivery_error == expected_error
            incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
            assert incident.acknowledged_at is None
            assert (
                await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
                is None
            )
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=incident_id, user_id=user_id
            )
            if case == "immediate_control":
                assert state is None
            else:
                assert state.current_stage == (1 if case == "later_stage" else 0)
                assert state.status == (
                    "exhausted" if case == "later_stage" else "running"
                )
                if case != "later_stage":
                    assert state.next_stage_due_at.replace(
                        tzinfo=timezone.utc
                    ) == now + timedelta(seconds=300)

        if expected_status == "skipped":
            assert calls == []
        else:
            assert len(calls) == 1
            assert calls[0]["From"] == "+15552222222"
            assert calls[0]["To"] == (
                "+15554444444" if case == "recipient_override" else "+15553333333"
            )
            menu = ET.fromstring(calls[0]["Twiml"])
            summary = "Voice Org. Critical severity incident"
            if case != "foreign_service":
                summary += " on Payments & API"
            summary += ": Database <pool> exhausted."
            assert summary in "".join(menu.itertext())
            gather = menu.find("Gather")
            if case == "no_public_url":
                assert gather is None
            else:
                assert gather is not None and gather.attrib["numDigits"] == "1"
                assert gather.attrib["method"] == "POST"
                spoken = gather.find("Say").text
                assert all(
                    text in spoken
                    for text in ("Press 1", "Press 2", "Press 3", "Press star")
                )
                callback = urlparse(gather.attrib["action"])
                assert callback.netloc == "paging.example.test"
                assert callback.path.startswith("/paging/voice/ack/")
                repeat = await client.post(callback.path, data={"Digits": "*"})
                assert repeat.status_code == 200
                assert summary in "".join(ET.fromstring(repeat.text).itertext())
                no_input = await client.post(callback.path, data={"Digits": ""})
                assert (
                    no_input.status_code == 200
                    and "No acknowledgement" in no_input.text
                )
                async with session_factory() as db:
                    unchanged = await IncidentRepo.get_by_id(
                        db, TEST_ORG_ID, incident_id
                    )
                    assert unchanged.acknowledged_at is None
                    assert (
                        await IncidentAssignmentRepo.get_active(
                            db, TEST_ORG_ID, incident_id
                        )
                        is None
                    )
                if expected_status == "sent":
                    ack = await client.post(callback.path, data={"Digits": "1"})
                    assert (
                        ack.status_code == 200 and "Incident acknowledged" in ack.text
                    )
                    async with session_factory() as db:
                        owned = await IncidentRepo.get_by_id(
                            db, TEST_ORG_ID, incident_id
                        )
                        assert owned.acknowledged_at is not None
                        assignment = await IncidentAssignmentRepo.get_active(
                            db, TEST_ORG_ID, incident_id
                        )
                        assert (
                            assignment is not None and assignment.assigned_to == user_id
                        )
                        if state is not None and case != "later_stage":
                            stopped = await NotificationEscalationRepo.get(
                                db,
                                TEST_ORG_ID,
                                incident_id=incident_id,
                                user_id=user_id,
                            )
                            assert (
                                stopped.status == "acked"
                                and stopped.next_stage_due_at is None
                            )

        async with session_factory() as db:
            fired = await ne.tick_all_due(
                db, sender=sender, at=now + timedelta(seconds=600)
            )
            await db.commit()
            assert fired == (
                1
                if case
                in {
                    "unconfigured",
                    "missing_phone",
                    "provider_rejected",
                    "no_public_url",
                }
                else 0
            )
            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
            assert sum(row.channel == "voice" for row in pages) == 1
        assert len(calls) == (0 if expected_status == "skipped" else 1)


@pytest.mark.integration
class TestStagedVoiceDeliveryPostgres(TestStagedVoiceDelivery):
    @pytest.fixture
    async def session_factory(self):
        url = os.environ.get("PART4_PG_URL")
        assert url, "Supply the isolated PostgreSQL fixture"
        database = make_url(url).database or ""
        # Hosted verification owns a fresh PostgreSQL service for this job.
        assert database.startswith("qa_") or (
            os.environ.get("GITHUB_ACTIONS") == "true" and database == "opsmender"
        ), "Use a disposable qa_ database locally"
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(Organization(id=TEST_ORG_ID, name="Voice Org", slug="voice-org"))
            await db.commit()
        try:
            yield factory
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()
