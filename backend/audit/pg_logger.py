"""PostgreSQL-backed audit logger for OpsMender AI.

Implements the same convenience methods as the JSONL ``AuditLogger`` but
persists entries to the ``audit_entries`` table via the async repository
layer.  Designed to be used as a drop-in replacement when a database
session is available.

The JSONL logger remains the default for CLI / offline use.  When running
under FastAPI (Phase 2), the API layer will instantiate this class
instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit.logger import AuditEntry as AuditEntryDC, AuditEntryType
from backend.db.models import AuditEntry as AuditEntryORM
from backend.db.repos import AuditEntryRepo


class PgAuditLogger:
    """Async audit logger backed by PostgreSQL.

    Parameters
    ----------
    db:
        An async SQLAlchemy session.  The caller is responsible for
        calling ``await db.commit()`` at the appropriate transaction
        boundary — this logger only flushes.
    """

    def __init__(self, db: AsyncSession, org_id: uuid.UUID) -> None:
        self._db = db
        self._org_id = org_id

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _entry_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- write methods (mirror AuditLogger API) -----------------------------

    async def log_tool_call_start(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
    ) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=AuditEntryType.TOOL_CALL_START.value,
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            permitted=True,
        )
        return str(entry.id)

    async def log_tool_call_end(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        result: dict | None = None,
        duration_ms: int | None = None,
    ) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=AuditEntryType.TOOL_CALL_END.value,
            tool_name=tool_name,
            result=result,
            permitted=True,
            duration_ms=duration_ms,
        )
        return str(entry.id)

    async def log_tool_call_blocked(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
        block_reason: str | None = None,
    ) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=AuditEntryType.TOOL_CALL_BLOCKED.value,
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            permitted=False,
            block_reason=block_reason,
        )
        return str(entry.id)

    async def log_session_start(self, session_id: str, tier: int) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=AuditEntryType.SESSION_START.value,
            permitted=True,
        )
        return str(entry.id)

    async def log_session_end(self, session_id: str, tier: int) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=AuditEntryType.SESSION_END.value,
            permitted=True,
        )
        return str(entry.id)

    async def log_workflow_step(
        self,
        session_id: str,
        tier: int,
        entry_type: AuditEntryType,
        tool_name: str,
        *,
        tool_parameters: dict | None = None,
        result: dict | None = None,
        permitted: bool = True,
        block_reason: str | None = None,
    ) -> str:
        entry = await AuditEntryRepo.create(
            self._db,
            self._org_id,
            session_id=uuid.UUID(session_id),
            tier=tier,
            entry_type=entry_type.value,
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            result=result,
            permitted=permitted,
            block_reason=block_reason,
        )
        return str(entry.id)

    # -- read methods -------------------------------------------------------

    async def read_by_session(self, session_id: str) -> list[AuditEntryDC]:
        """Return all entries for a given session as dataclass instances."""
        rows = await AuditEntryRepo.list_by_session(
            self._db, self._org_id, uuid.UUID(session_id)
        )
        return [self._orm_to_dc(row) for row in rows]

    async def query(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        entry_type: str | None = None,
        permitted: bool | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntryDC]:
        """Query audit entries with filters."""
        sid = uuid.UUID(session_id) if session_id else None
        rows = await AuditEntryRepo.query(
            self._db,
            self._org_id,
            session_id=sid,
            tool_name=tool_name,
            entry_type=entry_type,
            permitted=permitted,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
        return [self._orm_to_dc(row) for row in rows]

    # -- conversion ---------------------------------------------------------

    @staticmethod
    def _orm_to_dc(row: AuditEntryORM) -> AuditEntryDC:
        """Convert an ORM audit entry to the existing dataclass."""
        return AuditEntryDC(
            entry_id=str(row.id),
            session_id=str(row.session_id),
            timestamp=row.timestamp.isoformat(),
            tier=row.tier,
            entry_type=AuditEntryType(row.entry_type),
            tool_name=row.tool_name,
            tool_parameters=row.tool_parameters,
            result=row.result,
            permitted=row.permitted,
            block_reason=row.block_reason,
            duration_ms=row.duration_ms,
        )
