"""Tests for backend.db.repos — async repository CRUD operations.

Uses in-memory SQLite via aiosqlite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.models import Base, Session as SessionModel
from backend.db.repos import (
    ApprovalRequestRepo,
    AuditEntryRepo,
    IncidentRepo,
    ModelConfigRepo,
    SessionRepo,
    UserRepo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# UserRepo
# ---------------------------------------------------------------------------

class TestUserRepo:

    async def test_create_and_get(self, db: AsyncSession):
        user = await UserRepo.create(
            db, username="alice", email="alice@test.com", password_hash="h"
        )
        await db.flush()

        fetched = await UserRepo.get_by_id(db, user.id)
        assert fetched is not None
        assert fetched.username == "alice"

    async def test_get_by_username(self, db: AsyncSession):
        await UserRepo.create(
            db, username="bob", email="bob@test.com", password_hash="h"
        )
        await db.flush()

        fetched = await UserRepo.get_by_username(db, "bob")
        assert fetched is not None
        assert fetched.email == "bob@test.com"

    async def test_get_by_email(self, db: AsyncSession):
        await UserRepo.create(
            db, username="carol", email="carol@test.com", password_hash="h"
        )
        await db.flush()

        fetched = await UserRepo.get_by_email(db, "carol@test.com")
        assert fetched is not None
        assert fetched.username == "carol"

    async def test_get_nonexistent_returns_none(self, db: AsyncSession):
        assert await UserRepo.get_by_id(db, uuid.uuid4()) is None

    async def test_list_all(self, db: AsyncSession):
        await UserRepo.create(db, username="u1", email="u1@t.com", password_hash="h")
        await UserRepo.create(db, username="u2", email="u2@t.com", password_hash="h")
        await db.flush()

        users = await UserRepo.list_all(db)
        assert len(users) == 2


# ---------------------------------------------------------------------------
# IncidentRepo
# ---------------------------------------------------------------------------

class TestIncidentRepo:

    async def test_create_and_get(self, db: AsyncSession):
        inc = await IncidentRepo.create(
            db, title="Test", description="desc", severity="high"
        )
        await db.flush()

        fetched = await IncidentRepo.get_by_id(db, inc.id)
        assert fetched is not None
        assert fetched.title == "Test"
        assert fetched.status == "open"

    async def test_list_with_status_filter(self, db: AsyncSession):
        await IncidentRepo.create(db, title="A", description="d")
        inc2 = await IncidentRepo.create(db, title="B", description="d")
        await db.flush()

        await IncidentRepo.update_status(db, inc2.id, "resolved")
        await db.flush()

        open_incs = await IncidentRepo.list_all(db, status="open")
        assert len(open_incs) == 1
        assert open_incs[0].title == "A"

    async def test_list_pagination(self, db: AsyncSession):
        for i in range(5):
            await IncidentRepo.create(db, title=f"Inc-{i}", description="d")
        await db.flush()

        page = await IncidentRepo.list_all(db, limit=2, offset=0)
        assert len(page) == 2

        page2 = await IncidentRepo.list_all(db, limit=2, offset=2)
        assert len(page2) == 2

    async def test_update_status(self, db: AsyncSession):
        inc = await IncidentRepo.create(db, title="T", description="d")
        await db.flush()

        await IncidentRepo.update_status(db, inc.id, "closed")
        await db.flush()

        fetched = await IncidentRepo.get_by_id(db, inc.id)
        assert fetched.status == "closed"


# ---------------------------------------------------------------------------
# SessionRepo
# ---------------------------------------------------------------------------

class TestSessionRepo:

    async def test_create_and_get(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=2)
        await db.flush()

        fetched = await SessionRepo.get_by_id(db, sess.id)
        assert fetched is not None
        assert fetched.tier == 2
        assert fetched.status == "active"

    async def test_list_by_incident(self, db: AsyncSession):
        inc = await IncidentRepo.create(db, title="T", description="d")
        await db.flush()

        await SessionRepo.create(db, tier=2, incident_id=inc.id)
        await SessionRepo.create(db, tier=3, incident_id=inc.id)
        await db.flush()

        sessions = await SessionRepo.list_by_incident(db, inc.id)
        assert len(sessions) == 2

    async def test_end_session(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=2)
        await db.flush()
        sess_id = sess.id

        await SessionRepo.end_session(db, sess_id, status="completed", summary="done")
        await db.flush()

        await db.refresh(sess)
        assert sess.status == "completed"
        assert sess.summary == "done"
        assert sess.ended_at is not None


# ---------------------------------------------------------------------------
# AuditEntryRepo
# ---------------------------------------------------------------------------

class TestAuditEntryRepo:

    async def _make_session(self, db: AsyncSession) -> uuid.UUID:
        sess = await SessionRepo.create(db, tier=2)
        await db.flush()
        return sess.id

    async def test_create_and_list(self, db: AsyncSession):
        sid = await self._make_session(db)

        await AuditEntryRepo.create(
            db, session_id=sid, tier=2, entry_type="session_start"
        )
        await AuditEntryRepo.create(
            db,
            session_id=sid,
            tier=2,
            entry_type="tool_call_start",
            tool_name="get_pods",
        )
        await db.flush()

        entries = await AuditEntryRepo.list_by_session(db, sid)
        assert len(entries) == 2

    async def test_query_by_tool_name(self, db: AsyncSession):
        sid = await self._make_session(db)

        await AuditEntryRepo.create(
            db, session_id=sid, tier=2, entry_type="tool_call_end", tool_name="get_pods"
        )
        await AuditEntryRepo.create(
            db,
            session_id=sid,
            tier=2,
            entry_type="tool_call_blocked",
            tool_name="delete_pod",
            permitted=False,
        )
        await db.flush()

        results = await AuditEntryRepo.query(db, tool_name="delete_pod")
        assert len(results) == 1
        assert results[0].tool_name == "delete_pod"

    async def test_query_by_permitted(self, db: AsyncSession):
        sid = await self._make_session(db)

        await AuditEntryRepo.create(
            db, session_id=sid, tier=2, entry_type="tool_call_end", tool_name="get_pods"
        )
        await AuditEntryRepo.create(
            db,
            session_id=sid,
            tier=2,
            entry_type="tool_call_blocked",
            tool_name="delete_pod",
            permitted=False,
        )
        await db.flush()

        blocked = await AuditEntryRepo.query(db, permitted=False)
        assert len(blocked) == 1
        assert blocked[0].permitted is False

    async def test_query_pagination(self, db: AsyncSession):
        sid = await self._make_session(db)
        for i in range(5):
            await AuditEntryRepo.create(
                db, session_id=sid, tier=2, entry_type="tool_call_end", tool_name=f"t{i}"
            )
        await db.flush()

        page = await AuditEntryRepo.query(db, limit=2, offset=0)
        assert len(page) == 2


# ---------------------------------------------------------------------------
# ApprovalRequestRepo
# ---------------------------------------------------------------------------

class TestApprovalRequestRepo:

    async def test_create_and_list_pending(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=1)
        await db.flush()

        req = await ApprovalRequestRepo.create(
            db,
            session_id=sess.id,
            action={"tool": "delete_pod"},
            justification="CrashLoop",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.flush()

        pending = await ApprovalRequestRepo.list_pending(db)
        assert len(pending) == 1
        assert pending[0].id == req.id

    async def test_resolve_approval(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=1)
        user = await UserRepo.create(
            db, username="approver", email="a@t.com", password_hash="h", role="operator"
        )
        await db.flush()

        req = await ApprovalRequestRepo.create(
            db,
            session_id=sess.id,
            action={"tool": "delete_pod"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.flush()

        await ApprovalRequestRepo.resolve(
            db, req.id, status="approved", resolved_by=user.id
        )
        await db.flush()

        await db.refresh(req)
        assert req.status == "approved"
        assert req.resolved_at is not None
        assert req.resolved_by == user.id

    async def test_resolved_not_in_pending(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=1)
        await db.flush()

        req = await ApprovalRequestRepo.create(
            db,
            session_id=sess.id,
            action={"tool": "restart_pod"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.flush()

        await ApprovalRequestRepo.resolve(db, req.id, status="rejected")
        await db.flush()

        pending = await ApprovalRequestRepo.list_pending(db)
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# ModelConfigRepo
# ---------------------------------------------------------------------------

class TestModelConfigRepo:

    async def test_create_and_list(self, db: AsyncSession):
        await ModelConfigRepo.create(
            db, name="m1", provider="anthropic", model_id="claude-sonnet"
        )
        await ModelConfigRepo.create(
            db, name="m2", provider="openai", model_id="gpt-4o"
        )
        await db.flush()

        configs = await ModelConfigRepo.list_all(db)
        assert len(configs) == 2

    async def test_get_default(self, db: AsyncSession):
        await ModelConfigRepo.create(
            db, name="m1", provider="anthropic", model_id="s", is_default=True
        )
        await db.flush()

        default = await ModelConfigRepo.get_default(db)
        assert default is not None
        assert default.name == "m1"

    async def test_set_default(self, db: AsyncSession):
        c1 = await ModelConfigRepo.create(
            db, name="m1", provider="anthropic", model_id="s", is_default=True
        )
        c2 = await ModelConfigRepo.create(
            db, name="m2", provider="openai", model_id="g"
        )
        await db.flush()

        await ModelConfigRepo.set_default(db, c2.id)
        await db.flush()

        # Refresh objects from DB
        await db.refresh(c1)
        await db.refresh(c2)
        assert c1.is_default is False
        assert c2.is_default is True
