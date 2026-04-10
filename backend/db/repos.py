"""Async repository layer for all DB models.

Each repository wraps common CRUD operations for a single model using
an async SQLAlchemy session.  All methods accept a session and return
model instances or lists — the caller controls the transaction boundary.

Usage::

    async with session_factory() as session:
        user = await UserRepo.create(session, username="ops", ...)
        await session.commit()
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    ApprovalRequest,
    AuditEntry,
    Incident,
    ModelConfig,
    Session,
    User,
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str = "viewer",
    ) -> User:
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
        )
        db.add(user)
        await db.flush()
        return user

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
        return await db.get(User, user_id)

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[User]:
        stmt = select(User).order_by(User.created_at)
        result = await db.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

class IncidentRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        title: str,
        description: str,
        severity: str | None = None,
    ) -> Incident:
        incident = Incident(
            title=title,
            description=description,
            severity=severity,
        )
        db.add(incident)
        await db.flush()
        return incident

    @staticmethod
    async def get_by_id(db: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
        return await db.get(Incident, incident_id)

    @staticmethod
    async def list_all(
        db: AsyncSession,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        stmt = select(Incident).order_by(Incident.created_at.desc())
        if status:
            stmt = stmt.where(Incident.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_status(
        db: AsyncSession,
        incident_id: uuid.UUID,
        status: str,
    ) -> None:
        stmt = (
            update(Incident)
            .where(Incident.id == incident_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class SessionRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        tier: int,
        incident_id: uuid.UUID | None = None,
        model_provider: str | None = None,
        model_id: str | None = None,
    ) -> Session:
        session = Session(
            tier=tier,
            incident_id=incident_id,
            model_provider=model_provider,
            model_id=model_id,
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def get_by_id(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
        return await db.get(Session, session_id)

    @staticmethod
    async def list_by_incident(
        db: AsyncSession, incident_id: uuid.UUID
    ) -> Sequence[Session]:
        stmt = (
            select(Session)
            .where(Session.incident_id == incident_id)
            .order_by(Session.started_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def end_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        *,
        status: str = "completed",
        summary: str | None = None,
    ) -> None:
        stmt = (
            update(Session)
            .where(Session.id == session_id)
            .values(
                status=status,
                summary=summary,
                ended_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(stmt)


# ---------------------------------------------------------------------------
# Audit entries
# ---------------------------------------------------------------------------

class AuditEntryRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        tier: int,
        entry_type: str,
        tool_name: str | None = None,
        tool_parameters: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        permitted: bool = True,
        block_reason: str | None = None,
        duration_ms: int | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            session_id=session_id,
            tier=tier,
            entry_type=entry_type,
            tool_name=tool_name,
            tool_parameters=tool_parameters,
            result=result,
            permitted=permitted,
            block_reason=block_reason,
            duration_ms=duration_ms,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def list_by_session(
        db: AsyncSession,
        session_id: uuid.UUID,
    ) -> Sequence[AuditEntry]:
        stmt = (
            select(AuditEntry)
            .where(AuditEntry.session_id == session_id)
            .order_by(AuditEntry.timestamp)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def query(
        db: AsyncSession,
        *,
        session_id: uuid.UUID | None = None,
        tool_name: str | None = None,
        entry_type: str | None = None,
        permitted: bool | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AuditEntry]:
        stmt = select(AuditEntry).order_by(AuditEntry.timestamp.desc())
        if session_id:
            stmt = stmt.where(AuditEntry.session_id == session_id)
        if tool_name:
            stmt = stmt.where(AuditEntry.tool_name == tool_name)
        if entry_type:
            stmt = stmt.where(AuditEntry.entry_type == entry_type)
        if permitted is not None:
            stmt = stmt.where(AuditEntry.permitted == permitted)
        if start:
            stmt = stmt.where(AuditEntry.timestamp >= start)
        if end:
            stmt = stmt.where(AuditEntry.timestamp <= end)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Approval requests
# ---------------------------------------------------------------------------

class ApprovalRequestRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        action: dict[str, Any],
        justification: str | None = None,
        expires_at: datetime,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            session_id=session_id,
            action=action,
            justification=justification,
            expires_at=expires_at,
        )
        db.add(req)
        await db.flush()
        return req

    @staticmethod
    async def get_by_id(
        db: AsyncSession, request_id: uuid.UUID
    ) -> ApprovalRequest | None:
        return await db.get(ApprovalRequest, request_id)

    @staticmethod
    async def list_pending(db: AsyncSession) -> Sequence[ApprovalRequest]:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.requested_at)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def resolve(
        db: AsyncSession,
        request_id: uuid.UUID,
        *,
        status: str,
        resolved_by: uuid.UUID | None = None,
    ) -> None:
        stmt = (
            update(ApprovalRequest)
            .where(ApprovalRequest.id == request_id)
            .values(
                status=status,
                resolved_at=datetime.now(timezone.utc),
                resolved_by=resolved_by,
            )
        )
        await db.execute(stmt)


# ---------------------------------------------------------------------------
# Model configs
# ---------------------------------------------------------------------------

class ModelConfigRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        provider: str,
        model_id: str,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        is_default: bool = False,
    ) -> ModelConfig:
        cfg = ModelConfig(
            name=name,
            provider=provider,
            model_id=model_id,
            api_key_env_var=api_key_env_var,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            is_default=is_default,
        )
        db.add(cfg)
        await db.flush()
        return cfg

    @staticmethod
    async def get_default(db: AsyncSession) -> ModelConfig | None:
        stmt = select(ModelConfig).where(ModelConfig.is_default == True)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[ModelConfig]:
        stmt = select(ModelConfig).order_by(ModelConfig.name)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def set_default(
        db: AsyncSession, config_id: uuid.UUID
    ) -> None:
        # Clear existing default
        await db.execute(
            update(ModelConfig).values(is_default=False)
        )
        # Set new default
        await db.execute(
            update(ModelConfig)
            .where(ModelConfig.id == config_id)
            .values(is_default=True)
        )
