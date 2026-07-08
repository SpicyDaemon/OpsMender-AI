"""Tests for backend.db.models — verify ORM models can be created and queried.

Uses an in-memory SQLite database (via aiosqlite) so no Postgres is needed.
JSONB columns fall back to SQLAlchemy's JSON type on SQLite automatically.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.models import (
    ApprovalRequest,
    AuditEntry,
    Base,
    BotConnector,
    Incident,
    MCPServer,
    ModelConfig,
    Session,
    User,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")



@pytest.fixture
async def db():
    """Yield an async session backed by an in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


class TestUserModel:
    async def test_create_user(self, db: AsyncSession):
        user = User(
            username="testuser",
            email="test@example.com",
            password_hash="hashed123",
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        db.add(user)
        await db.flush()

        assert user.id is not None
        assert user.username == "testuser"
        assert user.role == "operator"
        assert user.is_active is True

    async def test_user_defaults(self, db: AsyncSession):
        user = User(
            username="default_user",
            email="default@example.com",
            password_hash="hashed",
            primary_org_id=TEST_ORG_ID,
        )
        db.add(user)
        await db.flush()

        assert user.role == "viewer"
        assert user.is_active is True
        assert user.created_at is not None


# ---------------------------------------------------------------------------
# Incident model
# ---------------------------------------------------------------------------


class TestIncidentModel:
    async def test_create_incident(self, db: AsyncSession):
        inc = Incident(
            org_id=TEST_ORG_ID,
            title="Test incident",
            description="Something broke",
            severity="high",
        )
        db.add(inc)
        await db.flush()

        assert inc.id is not None
        assert inc.status == "open"
        assert inc.severity == "high"

    async def test_incident_defaults(self, db: AsyncSession):
        inc = Incident(
            org_id=TEST_ORG_ID,
            title="Minimal",
            description="desc",
        )
        db.add(inc)
        await db.flush()

        assert inc.status == "open"
        assert inc.severity is None
        assert inc.created_at is not None


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class TestSessionModel:
    async def test_create_session(self, db: AsyncSession):
        sess = Session(org_id=TEST_ORG_ID, tier=2, model_provider="anthropic", model_id="claude-sonnet")
        db.add(sess)
        await db.flush()

        assert sess.id is not None
        assert sess.tier == 2
        assert sess.status == "active"
        assert sess.ended_at is None

    async def test_session_with_incident(self, db: AsyncSession):
        inc = Incident(org_id=TEST_ORG_ID, title="Parent", description="d")
        db.add(inc)
        await db.flush()

        sess = Session(org_id=TEST_ORG_ID, tier=3, incident_id=inc.id)
        db.add(sess)
        await db.flush()

        assert sess.incident_id == inc.id


# ---------------------------------------------------------------------------
# AuditEntry model
# ---------------------------------------------------------------------------


class TestAuditEntryModel:
    async def test_create_audit_entry(self, db: AsyncSession):
        sess = Session(org_id=TEST_ORG_ID, tier=2)
        db.add(sess)
        await db.flush()

        entry = AuditEntry(
            org_id=TEST_ORG_ID,
            session_id=sess.id,
            tier=2,
            entry_type="tool_call_start",
            tool_name="get_pods",
            tool_parameters={"namespace": "default"},
            permitted=True,
        )
        db.add(entry)
        await db.flush()

        assert entry.id is not None
        assert entry.tool_name == "get_pods"
        assert entry.permitted is True

    async def test_blocked_entry(self, db: AsyncSession):
        sess = Session(org_id=TEST_ORG_ID, tier=2)
        db.add(sess)
        await db.flush()

        entry = AuditEntry(
            org_id=TEST_ORG_ID,
            session_id=sess.id,
            tier=2,
            entry_type="tool_call_blocked",
            tool_name="delete_pod",
            permitted=False,
            block_reason="Tier 2 denies destructive operations",
        )
        db.add(entry)
        await db.flush()

        assert entry.permitted is False
        assert entry.block_reason == "Tier 2 denies destructive operations"


# ---------------------------------------------------------------------------
# ApprovalRequest model
# ---------------------------------------------------------------------------


class TestApprovalRequestModel:
    async def test_create_approval_request(self, db: AsyncSession):
        sess = Session(org_id=TEST_ORG_ID, tier=1)
        db.add(sess)
        await db.flush()

        req = ApprovalRequest(
            org_id=TEST_ORG_ID,
            session_id=sess.id,
            action={"tool": "delete_pod", "params": {"pod": "web-1"}},
            justification="Pod is stuck in CrashLoopBackOff",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.add(req)
        await db.flush()

        assert req.id is not None
        assert req.status == "pending"
        assert req.resolved_at is None


# ---------------------------------------------------------------------------
# ModelConfig model
# ---------------------------------------------------------------------------


class TestModelConfigModel:
    async def test_create_model_config(self, db: AsyncSession):
        cfg = ModelConfig(
            org_id=TEST_ORG_ID,
            name="test-model",
            provider="anthropic",
            model_id="claude-sonnet-4-20250514",
            api_key_env_var="ANTHROPIC_API_KEY",
            api_version="2024-10-21",
            is_default=True,
        )
        db.add(cfg)
        await db.flush()

        assert cfg.id is not None
        assert cfg.provider == "anthropic"
        assert cfg.api_version == "2024-10-21"
        assert cfg.max_tokens == 4096
        assert cfg.temperature == 0.0
        assert cfg.is_default is True


# ---------------------------------------------------------------------------
# MCPServer model
# ---------------------------------------------------------------------------


class TestMCPServerModel:
    async def test_create_mcp_server(self, db: AsyncSession):
        server = MCPServer(
            org_id=TEST_ORG_ID,
            name="k8s-prod",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic/mcp-server-k8s"],
            env_vars={"KUBECONFIG": "/tmp/config"},
            is_active=True,
        )
        db.add(server)
        await db.flush()

        assert server.id is not None
        assert server.name == "k8s-prod"
        assert server.transport == "stdio"
        assert server.command == "npx"
        assert server.args == ["-y", "@anthropic/mcp-server-k8s"]
        assert server.env_vars == {"KUBECONFIG": "/tmp/config"}
        assert server.is_active is True


# ---------------------------------------------------------------------------
# BotConnector model
# ---------------------------------------------------------------------------


class TestBotConnectorModel:
    async def test_create_bot_connector(self, db: AsyncSession):
        connector = BotConnector(
            org_id=TEST_ORG_ID,
            name="telegram-primary",
            platform="telegram",
            config={"default_chat_id": "-100123"},
            credentials={"bot_token": "secret"},
            allowed_capabilities=["incident_lookup", "approvals"],
            status="configured",
            is_enabled=True,
        )
        db.add(connector)
        await db.flush()

        assert connector.id is not None
        assert connector.platform == "telegram"
        assert connector.config == {"default_chat_id": "-100123"}
        assert connector.credentials == {"bot_token": "secret"}
        assert connector.allowed_capabilities == ["incident_lookup", "approvals"]
        assert connector.status == "configured"
        assert connector.is_enabled is True
