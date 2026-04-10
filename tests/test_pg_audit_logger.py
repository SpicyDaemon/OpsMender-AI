"""Tests for backend.audit.pg_logger — Postgres-backed audit logger."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.audit.logger import AuditEntryType
from backend.audit.pg_logger import PgAuditLogger
from backend.db.models import Base, Session as SessionModel
from backend.db.repos import SessionRepo


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def session_id(db: AsyncSession) -> str:
    """Create a session row and return its ID as a string."""
    sess = await SessionRepo.create(db, tier=2)
    await db.flush()
    return str(sess.id)


class TestPgAuditLogger:

    async def test_log_session_lifecycle(self, db: AsyncSession, session_id: str):
        logger = PgAuditLogger(db)

        start_id = await logger.log_session_start(session_id, tier=2)
        assert start_id  # non-empty string

        end_id = await logger.log_session_end(session_id, tier=2)
        assert end_id

        entries = await logger.read_by_session(session_id)
        assert len(entries) == 2
        assert entries[0].entry_type == AuditEntryType.SESSION_START
        assert entries[1].entry_type == AuditEntryType.SESSION_END

    async def test_log_tool_call(self, db: AsyncSession, session_id: str):
        logger = PgAuditLogger(db)

        await logger.log_tool_call_start(
            session_id, tier=2, tool_name="get_pods", tool_parameters={"ns": "default"}
        )
        await logger.log_tool_call_end(
            session_id, tier=2, tool_name="get_pods", result={"count": 5}, duration_ms=150
        )

        entries = await logger.read_by_session(session_id)
        assert len(entries) == 2
        assert entries[0].tool_name == "get_pods"
        assert entries[1].duration_ms == 150

    async def test_log_blocked_call(self, db: AsyncSession, session_id: str):
        logger = PgAuditLogger(db)

        await logger.log_tool_call_blocked(
            session_id,
            tier=2,
            tool_name="delete_pod",
            tool_parameters={"pod": "web-1"},
            block_reason="Tier 2 denies destructive operations",
        )

        entries = await logger.read_by_session(session_id)
        assert len(entries) == 1
        assert entries[0].permitted is False
        assert entries[0].block_reason == "Tier 2 denies destructive operations"
        assert entries[0].entry_type == AuditEntryType.TOOL_CALL_BLOCKED

    async def test_query_filters(self, db: AsyncSession, session_id: str):
        logger = PgAuditLogger(db)

        await logger.log_tool_call_start(session_id, 2, "get_pods")
        await logger.log_tool_call_blocked(
            session_id, 2, "delete_pod", block_reason="denied"
        )

        blocked = await logger.query(permitted=False)
        assert len(blocked) == 1
        assert blocked[0].tool_name == "delete_pod"

        by_tool = await logger.query(tool_name="get_pods")
        assert len(by_tool) == 1

    async def test_returns_dataclass_instances(self, db: AsyncSession, session_id: str):
        logger = PgAuditLogger(db)
        await logger.log_session_start(session_id, tier=2)

        entries = await logger.read_by_session(session_id)
        from backend.audit.logger import AuditEntry as AuditEntryDC
        assert isinstance(entries[0], AuditEntryDC)
