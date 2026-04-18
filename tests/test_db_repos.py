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
    DetectorHistoryRepo,
    DetectorRuleRepo,
    IncidentRepo,
    MCPServerRepo,
    ModelConfigRepo,
    SessionRepo,
    UserRepo,
    WebhookTriggerRepo,
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

    async def test_set_status(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=1)
        await db.flush()

        await SessionRepo.set_status(db, sess.id, status="awaiting_approval")
        await db.flush()
        await db.refresh(sess)

        assert sess.status == "awaiting_approval"


# ---------------------------------------------------------------------------
# WebhookTriggerRepo
# ---------------------------------------------------------------------------

class TestWebhookTriggerRepo:

    async def test_create_and_list_matching_event(self, db: AsyncSession):
        trigger = await WebhookTriggerRepo.create(
            db,
            name="session-complete",
            url="https://example.com/hook",
            event_types=["session.completed"],
            headers={"X-Test": "1"},
            token="secret",
        )
        await db.flush()

        items = await WebhookTriggerRepo.list_matching_event(db, "session.completed")
        assert [item.id for item in items] == [trigger.id]

    async def test_mark_delivery_updates_timestamp_and_error(self, db: AsyncSession):
        trigger = await WebhookTriggerRepo.create(
            db,
            name="session-fail",
            url="https://example.com/fail",
            event_types=["session.failed"],
        )
        await db.flush()

        await WebhookTriggerRepo.mark_delivery(
            db,
            trigger.id,
            error="boom",
        )
        await db.flush()
        await db.refresh(trigger)

        assert trigger.last_triggered_at is not None
        assert trigger.last_error == "boom"


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

    async def test_list_filters(self, db: AsyncSession):
        sess1 = await SessionRepo.create(db, tier=1)
        sess2 = await SessionRepo.create(db, tier=1)
        await db.flush()

        req1 = await ApprovalRequestRepo.create(
            db,
            session_id=sess1.id,
            action={"tool": "delete_pod"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        req2 = await ApprovalRequestRepo.create(
            db,
            session_id=sess2.id,
            action={"tool": "restart_pod"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.flush()
        await ApprovalRequestRepo.resolve(db, req2.id, status="approved")
        await db.flush()

        pending = await ApprovalRequestRepo.list(db, status="pending", session_id=sess1.id)
        assert len(pending) == 1
        assert pending[0].id == req1.id

    async def test_resolve_returns_false_for_non_pending(self, db: AsyncSession):
        sess = await SessionRepo.create(db, tier=1)
        await db.flush()

        req = await ApprovalRequestRepo.create(
            db,
            session_id=sess.id,
            action={"tool": "restart_pod"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.flush()

        first = await ApprovalRequestRepo.resolve(db, req.id, status="approved")
        second = await ApprovalRequestRepo.resolve(db, req.id, status="rejected")

        assert first is True
        assert second is False


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

    async def test_get_by_name(self, db: AsyncSession):
        await ModelConfigRepo.create(
            db,
            name="azure-prod",
            provider="azure_openai",
            model_id="gpt-4o",
            api_version="2024-10-21",
        )
        await db.flush()

        cfg = await ModelConfigRepo.get_by_name(db, "azure-prod")
        assert cfg is not None
        assert cfg.provider == "azure_openai"
        assert cfg.api_version == "2024-10-21"

    async def test_upsert_creates_when_missing(self, db: AsyncSession):
        cfg = await ModelConfigRepo.upsert(
            db,
            name="local",
            provider="ollama",
            model_id="llama3.2",
            base_url="http://localhost:11434",
        )
        await db.flush()

        assert cfg.name == "local"
        assert cfg.provider == "ollama"
        assert cfg.base_url == "http://localhost:11434"

    async def test_upsert_updates_existing(self, db: AsyncSession):
        created = await ModelConfigRepo.create(
            db,
            name="shared",
            provider="openai",
            model_id="gpt-4o-mini",
            api_key_env_var="OPENAI_API_KEY",
        )
        await db.flush()

        updated = await ModelConfigRepo.upsert(
            db,
            name="shared",
            provider="azure_openai",
            model_id="deployment-gpt4",
            api_key_env_var="AZURE_OPENAI_API_KEY",
            base_url="https://example-resource.openai.azure.com/",
            api_version="2024-10-21",
            max_tokens=8192,
            temperature=0.2,
            is_default=True,
        )
        await db.flush()

        assert updated.id == created.id
        assert updated.provider == "azure_openai"
        assert updated.model_id == "deployment-gpt4"
        assert updated.api_key_env_var == "AZURE_OPENAI_API_KEY"
        assert updated.base_url == "https://example-resource.openai.azure.com/"
        assert updated.api_version == "2024-10-21"
        assert updated.max_tokens == 8192
        assert updated.temperature == 0.2
        assert updated.is_default is True


# ---------------------------------------------------------------------------
# MCPServerRepo
# ---------------------------------------------------------------------------

class TestMCPServerRepo:

    async def test_create_and_list(self, db: AsyncSession):
        await MCPServerRepo.create(
            db,
            name="k8s",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic/mcp-server-k8s"],
        )
        await MCPServerRepo.create(
            db,
            name="sourcebot",
            transport="http",
            url="https://sb.example.com/api/mcp",
            is_active=False,
        )
        await db.flush()

        servers = await MCPServerRepo.list_all(db)
        assert len(servers) == 2

        active_servers = await MCPServerRepo.list_all(db, active_only=True)
        assert len(active_servers) == 1
        assert active_servers[0].name == "k8s"


# ---------------------------------------------------------------------------
# Detector repos
# ---------------------------------------------------------------------------

class TestDetectorRepos:

    async def test_create_rule_and_history(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db,
            name="detector-k8s",
            transport="stdio",
            command="echo",
        )
        await db.flush()

        rule = await DetectorRuleRepo.create(
            db,
            name="watch-crashloops",
            mcp_server_id=server.id,
            prompt_template="Find unhealthy pods",
            interval_seconds=600,
            severity_default="high",
        )
        await db.flush()

        fetched = await DetectorRuleRepo.get_by_id(db, rule.id)
        assert fetched is not None
        assert fetched.name == "watch-crashloops"
        assert fetched.interval_seconds == 600

        history = await DetectorHistoryRepo.create(
            db,
            rule_id=rule.id,
            duration_ms=123,
            issue_detected=True,
            raw_verdict={"issue_detected": True, "fingerprint": "abc"},
        )
        await db.flush()

        entries = await DetectorHistoryRepo.list_by_rule(db, rule.id)
        assert len(entries) == 1
        assert entries[0].id == history.id
        assert entries[0].issue_detected is True

    async def test_update_mark_run_and_delete_rule(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db,
            name="detector-db",
            transport="stdio",
            command="echo",
        )
        await db.flush()

        rule = await DetectorRuleRepo.create(
            db,
            name="watch-errors",
            mcp_server_id=server.id,
            prompt_template="Find database issues",
        )
        await db.flush()

        updated = await DetectorRuleRepo.update(
            db,
            rule.id,
            interval_seconds=900,
            is_active=False,
        )
        await db.flush()
        assert updated is not None
        assert updated.interval_seconds == 900
        assert updated.is_active is False

        await DetectorRuleRepo.mark_run(
            db,
            rule.id,
            last_fingerprint="fp-123",
        )
        await db.flush()
        await db.refresh(rule)
        assert rule.last_ran_at is not None
        assert rule.last_fingerprint == "fp-123"

        deleted = await DetectorRuleRepo.delete(db, rule.id)
        await db.flush()
        assert deleted is True
        assert await DetectorRuleRepo.get_by_id(db, rule.id) is None

    async def test_update_server(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db,
            name="remote",
            transport="sse",
            url="http://localhost:8080/sse",
        )
        await db.flush()

        updated = await MCPServerRepo.update(
            db,
            server.id,
            name="remote-prod",
            transport="http",
            url="https://mcp.example.com/api/mcp",
            token="secret",
            env_vars={"DEBUG": "1"},
            is_active=True,
        )
        await db.flush()

        assert updated is not None
        assert updated.name == "remote-prod"
        assert updated.transport == "http"
        assert updated.token == "secret"
        assert updated.env_vars == {"DEBUG": "1"}

    async def test_delete_server(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db,
            name="delete-me",
            transport="stdio",
            command="echo",
        )
        await db.flush()

        deleted = await MCPServerRepo.delete(db, server.id)
        await db.flush()

        assert deleted is True
        assert await MCPServerRepo.get_by_id(db, server.id) is None
