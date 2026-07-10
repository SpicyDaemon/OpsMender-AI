"""Tests for Sprint 12 Feature 4 — Co-pilot Chat.

Covers:

* ``SessionMessageRepo`` — create/list/pending/mark_consumed.
* ``POST /sessions`` — ``initial_briefing`` seeds a user message.
* ``POST /sessions/{id}/messages`` — RBAC + payload shape + DB persistence.
* ``GET  /sessions/{id}/messages`` — list endpoint.
* ``backend.chat.responder.respond_to_user_message`` — builds prompt,
  writes assistant reply, publishes WS events (verified via a recording
  ``publish``).
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.api.routes import ws as ws_module
from backend.chat.responder import respond_to_user_message
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import (
    IncidentRepo,
    SessionMessageRepo,
    SessionRepo,
)
from backend.llm.factory import create_llm


# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_api.py setup)
# ---------------------------------------------------------------------------

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "copilot-chat.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from backend.db.models import Organization

        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    def _override_get_db():
        async def _get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        return _get_db

    application.dependency_overrides[get_db] = _override_get_db()

    yield application

    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_and_login(
    client: AsyncClient, *, username: str, role: str = "viewer"
) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "secretpass123",
            "role": role,
        },
    )
    # Linked automatically by /auth/register

    resp = await client.post(
        "/auth/login",
        json={"username": username, "password": "secretpass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client: AsyncClient) -> dict[str, str]:
    # The very first registered user is auto-promoted to admin.
    return await _register_and_login(client, username="adminchat")


@pytest.fixture
async def operator_headers(client: AsyncClient, admin_headers) -> dict[str, str]:
    return await _register_and_login(client, username="opchat", role="operator")


@pytest.fixture
async def viewer_headers(client: AsyncClient, admin_headers) -> dict[str, str]:
    return await _register_and_login(client, username="viewerchat", role="viewer")


@pytest.fixture(autouse=True)
def _stub_publish(monkeypatch):
    """Capture every WS publish call so the route tests stay deterministic."""
    calls: list[tuple[uuid.UUID, dict]] = []

    async def _recording_publish(session_id, message):
        calls.append((session_id, message.model_dump()))

    monkeypatch.setattr(ws_module, "publish", _recording_publish)
    # sessions.py and responder.py import publish directly — patch those too.
    import backend.api.routes.sessions as sessions_module
    import backend.chat.responder as responder_module

    monkeypatch.setattr(sessions_module, "publish", _recording_publish)
    monkeypatch.setattr(responder_module, "publish", _recording_publish)
    return calls


@pytest.fixture(autouse=True)
def _neutralise_background_responder(monkeypatch):
    """Don't actually schedule the chat responder in route tests."""
    import backend.api.routes.sessions as sessions_module

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(sessions_module, "respond_to_user_message", _noop)


# ---------------------------------------------------------------------------
# Repo tests
# ---------------------------------------------------------------------------


class TestSessionMessageRepo:
    async def test_create_and_list(self, app):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            m1 = await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="user",
                content="hello",
            )
            m2 = await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="assistant",
                content="hi there",
            )
            await db.commit()

            items = await SessionMessageRepo.list_by_session(
                db, TEST_ORG_ID, session.id
            )
            assert [m.id for m in items] == [m1.id, m2.id]
            assert items[0].consumed_by_workflow is False

    async def test_list_pending_only_returns_user(self, app):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await SessionMessageRepo.create(
                db, TEST_ORG_ID, session_id=session.id, role="user", content="pending"
            )
            await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="user",
                content="already seen",
                consumed_by_workflow=True,
            )
            await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="assistant",
                content="reply",
            )
            await db.commit()

            pending = await SessionMessageRepo.list_pending_user(
                db, TEST_ORG_ID, session.id
            )
            assert [m.content for m in pending] == ["pending"]

    async def test_mark_consumed_flips_flag(self, app):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await SessionMessageRepo.create(
                db, TEST_ORG_ID, session_id=session.id, role="user", content="a"
            )
            await SessionMessageRepo.create(
                db, TEST_ORG_ID, session_id=session.id, role="user", content="b"
            )
            await db.commit()

            count = await SessionMessageRepo.mark_consumed(
                db, TEST_ORG_ID, session.id, node_context="diagnose"
            )
            await db.commit()
            assert count == 2

            pending = await SessionMessageRepo.list_pending_user(
                db, TEST_ORG_ID, session.id
            )
            assert pending == []

            all_msgs = await SessionMessageRepo.list_by_session(
                db, TEST_ORG_ID, session.id
            )
            assert all(m.consumed_by_workflow for m in all_msgs)
            assert all(m.node_context == "diagnose" for m in all_msgs)


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


class TestChatRoutes:
    async def test_initial_briefing_seeds_user_message(
        self, client: AsyncClient, admin_headers, app
    ):
        resp = await client.post(
            "/sessions",
            json={"tier": 2, "initial_briefing": "DB pool exhausted at 09:00"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        factory = app.state.session_factory
        async with factory() as db:
            msgs = await SessionMessageRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(session_id)
            )
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == "DB pool exhausted at 09:00"
        assert msgs[0].node_context == "initial_briefing"

    async def test_initial_briefing_empty_creates_no_message(
        self, client: AsyncClient, admin_headers, app
    ):
        resp = await client.post(
            "/sessions",
            json={"tier": 2, "initial_briefing": "   "},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        factory = app.state.session_factory
        async with factory() as db:
            msgs = await SessionMessageRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(session_id)
            )
        assert msgs == []

    async def test_post_message_persists_and_publishes(
        self, client: AsyncClient, admin_headers, app, _stub_publish
    ):
        create = await client.post("/sessions", json={"tier": 2}, headers=admin_headers)
        session_id = create.json()["id"]

        resp = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "what's the blast radius?"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["role"] == "user"
        assert body["content"] == "what's the blast radius?"
        assert body["consumed_by_workflow"] is False

        # Published event for the user turn.
        user_events = [
            m for (_, m) in _stub_publish if m["type"] == "chat_message_user"
        ]
        assert len(user_events) == 1
        assert user_events[0]["data"]["content"] == "what's the blast radius?"

        # DB shows a single user row.
        factory = app.state.session_factory
        async with factory() as db:
            msgs = await SessionMessageRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(session_id)
            )
        assert len(msgs) == 1
        assert msgs[0].role == "user"

    async def test_operator_can_post(
        self, client: AsyncClient, admin_headers, operator_headers
    ):
        create = await client.post("/sessions", json={"tier": 2}, headers=admin_headers)
        session_id = create.json()["id"]

        resp = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "operator input"},
            headers=operator_headers,
        )
        assert resp.status_code == 201

    async def test_viewer_cannot_post(
        self, client: AsyncClient, admin_headers, viewer_headers
    ):
        create = await client.post("/sessions", json={"tier": 2}, headers=admin_headers)
        session_id = create.json()["id"]

        resp = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "nope"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_viewer_cannot_read_chat(
        self, client: AsyncClient, admin_headers, viewer_headers
    ):
        # Co-pilot chat is AI session content — Viewers are forbidden (Part 1).
        create = await client.post("/sessions", json={"tier": 2}, headers=admin_headers)
        session_id = create.json()["id"]
        await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "audit trail"},
            headers=admin_headers,
        )

        resp = await client.get(
            f"/sessions/{session_id}/messages", headers=viewer_headers
        )
        assert resp.status_code == 403

    async def test_post_to_unknown_session_404(
        self, client: AsyncClient, admin_headers
    ):
        resp = await client.post(
            f"/sessions/{uuid.uuid4()}/messages",
            json={"content": "hi"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_empty_content_rejected(self, client: AsyncClient, admin_headers):
        create = await client.post("/sessions", json={"tier": 2}, headers=admin_headers)
        session_id = create.json()["id"]

        resp = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": ""},
            headers=admin_headers,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Responder tests (no route, no background scheduling)
# ---------------------------------------------------------------------------


class TestResponder:
    async def test_responder_saves_assistant_and_publishes(self, app, _stub_publish):
        factory = app.state.session_factory
        async with factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Checkout 5xx",
                description="Error rate climbing",
                severity="high",
            )
            session = await SessionRepo.create(
                db, TEST_ORG_ID, tier=2, incident_id=incident.id
            )
            user_msg = await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="user",
                content="What service is affected?",
            )
            await db.commit()
            session_id = session.id
            user_msg_id = user_msg.id

        # Inject a deterministic stub LLM.
        stub = create_llm(provider="stub", response="checkout-api in us-east-1")

        await respond_to_user_message(
            factory,
            org_id=TEST_ORG_ID,
            session_id=session_id,
            user_message_id=user_msg_id,
            llm_factory=lambda: stub,
        )

        async with factory() as db:
            msgs = await SessionMessageRepo.list_by_session(db, TEST_ORG_ID, session_id)
        roles = [m.role for m in msgs]
        contents = [m.content for m in msgs]
        assert roles == ["user", "assistant"]
        assert contents[1] == "checkout-api in us-east-1"

        assistant_events = [
            m for (_, m) in _stub_publish if m["type"] == "chat_message_assistant"
        ]
        assert len(assistant_events) == 1
        assert assistant_events[0]["data"]["content"] == "checkout-api in us-east-1"
        assert assistant_events[0]["data"]["role"] == "assistant"

    async def test_responder_publishes_error_on_missing_session(
        self, app, _stub_publish
    ):
        factory = app.state.session_factory
        fake_session = uuid.uuid4()
        fake_msg = uuid.uuid4()

        stub = create_llm(provider="stub", response="unreachable")
        await respond_to_user_message(
            factory,
            org_id=TEST_ORG_ID,
            session_id=fake_session,
            user_message_id=fake_msg,
            llm_factory=lambda: stub,
        )

        err_events = [m for (_, m) in _stub_publish if m["type"] == "error"]
        assert any("copilot_chat" == m["data"].get("source") for m in err_events)

    async def test_responder_handles_empty_llm_reply(self, app, _stub_publish):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            user_msg = await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="user",
                content="ping",
            )
            await db.commit()
            session_id = session.id
            user_msg_id = user_msg.id

        stub = create_llm(provider="stub", response="   ")
        await respond_to_user_message(
            factory,
            org_id=TEST_ORG_ID,
            session_id=session_id,
            user_message_id=user_msg_id,
            llm_factory=lambda: stub,
        )

        async with factory() as db:
            msgs = await SessionMessageRepo.list_by_session(db, TEST_ORG_ID, session_id)
        assert msgs[-1].content == "[co-pilot returned no content]"
