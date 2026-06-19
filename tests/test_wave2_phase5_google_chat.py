"""Wave 2 Phase 5 — Google Chat Respond/Track delivery tests."""

from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.bots import notifier
from backend.bots.capabilities import get_platform_capabilities
from backend.bots.connectors import get_adapter
from backend.bots.connectors.google_chat import GoogleChatAdapter
from backend.db.models import Base, BotConnector, IncidentNotificationReceipt
from backend.db.repos import BotConnectorRepo, IncidentRepo, IncidentTrackPostRepo

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _connector(private_key: str) -> BotConnector:
    return BotConnector(
        id=uuid.uuid4(),
        org_id=TEST_ORG_ID,
        name="google-chat",
        platform="google_chat",
        config={"default_chat_id": "spaces/SPACE1"},
        credentials={
            "client_email": "chat@project.iam.gserviceaccount.com",
            "private_key": private_key,
        },
        allowed_capabilities=["notifications"],
        lanes=["respond", "track"],
        status="configured",
        is_enabled=True,
    )


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_google_chat_creates_and_updates_durable_message():
    seen: list[httpx.Request] = []
    assertions: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assertions.append(parse_qs(request.content.decode())["assertion"][0])
            return httpx.Response(200, json={"access_token": "chat-token"})
        assert request.headers["authorization"] == "Bearer chat-token"
        if request.method == "GET" and request.url.path.endswith("/spaces/SPACE1"):
            return httpx.Response(200, json={"name": "spaces/SPACE1"})
        if request.method == "POST":
            assert json.loads(request.content) == {"text": "API outage"}
            return httpx.Response(
                200,
                json={
                    "name": "spaces/SPACE1/messages/MSG1",
                    "thread": {"name": "spaces/SPACE1/threads/THREAD1"},
                },
            )
        if request.method == "PATCH":
            assert request.url.params["updateMask"] == "text"
            assert json.loads(request.content) == {"text": "API restored"}
            return httpx.Response(200, json={"name": "spaces/SPACE1/messages/MSG1"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = GoogleChatAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(_private_key())
    assert await adapter.test_connection(connector) == (True, None)
    receipt = await adapter.send_incident_update(
        connector,
        chat_id="spaces/SPACE1",
        text="API outage",
    )
    assert receipt.ok is True
    assert receipt.external_message_id == "spaces/SPACE1/messages/MSG1"
    assert receipt.external_thread_id == "spaces/SPACE1/threads/THREAD1"
    assert receipt.can_update is True
    updated = await adapter.update_incident_update(
        connector,
        chat_id="spaces/SPACE1",
        text="API restored",
        external_message_id=receipt.external_message_id,
        external_thread_id=receipt.external_thread_id,
    )
    assert updated.ok is True
    assert updated.receipt is not None
    assert updated.receipt.external_message_id == "spaces/SPACE1/messages/MSG1"
    claims = jwt.decode(assertions[0], options={"verify_signature": False})
    assert claims["scope"] == "https://www.googleapis.com/auth/chat.bot"


async def test_google_chat_track_delivery_updates_one_stored_message(
    factory, monkeypatch
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "chat-token"})
        if request.method == "POST":
            return httpx.Response(200, json={"name": "spaces/SPACE1/messages/MSG1"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"name": "spaces/SPACE1/messages/MSG1"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = get_adapter("google_chat")
    monkeypatch.setattr(
        adapter,
        "_factory",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="google-chat-track",
            platform="google_chat",
            config={"default_chat_id": "spaces/SPACE1"},
            credentials={
                "client_email": "chat@project.iam.gserviceaccount.com",
                "private_key": _private_key(),
            },
            allowed_capabilities=["notifications"],
            lanes=["track"],
            status="configured",
            is_enabled=True,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="API outage",
            description="Elevated errors",
            severity="high",
        )
        await db.commit()
        connector_id = connector.id
        incident_id = incident.id

    await notifier.deliver_incident_event(
        factory,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        event_type="incident.created",
    )
    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        incident.status = "resolved"
        await db.commit()
    await notifier.deliver_incident_event(
        factory,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        event_type="incident.resolved",
    )

    provider_methods = [
        request.method
        for request in requests
        if request.url.host == "chat.googleapis.com"
    ]
    assert provider_methods == ["POST", "PATCH"]
    async with factory() as db:
        track = await IncidentTrackPostRepo.get(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            connector_id=connector_id,
        )
        assert track is not None
        assert track.external_message_id == "spaces/SPACE1/messages/MSG1"
        receipts = (
            (
                await db.execute(
                    select(IncidentNotificationReceipt)
                    .where(
                        IncidentNotificationReceipt.incident_id == incident_id,
                        IncidentNotificationReceipt.connector_id == connector_id,
                    )
                    .order_by(IncidentNotificationReceipt.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [item.delivery_status for item in receipts] == [
            "delivered",
            "updated",
        ]


def test_google_chat_registration_schema_and_capabilities():
    adapter = get_adapter("google_chat")
    assert isinstance(adapter, GoogleChatAdapter)
    fields = {field.name: field for field in adapter.form_schema()}
    assert fields["default_chat_id"].required is True
    assert fields["client_email"].group == "credentials"
    assert fields["private_key"].kind == "textarea"
    capabilities = get_platform_capabilities("google_chat")
    assert capabilities is not None
    assert capabilities.shared_channel is True
    assert capabilities.message_update is True
    assert capabilities.interactive_actions is False
