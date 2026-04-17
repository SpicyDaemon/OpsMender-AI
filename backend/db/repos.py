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
    DetectorHistory,
    DetectorRule,
    Incident,
    IngestLog,
    IngestToken,
    MCPServer,
    ModelConfig,
    RuntimeConfig,
    Session,
    SessionMessage,
    Skill,
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

    @staticmethod
    async def get_by_external_fingerprint(
        db: AsyncSession,
        *,
        external_source: str,
        external_id: str,
    ) -> Incident | None:
        """Look up an incident by its external fingerprint for dedup."""
        stmt = (
            select(Incident)
            .where(
                Incident.external_source == external_source,
                Incident.external_id == external_id,
            )
            .order_by(Incident.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


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

    @staticmethod
    async def set_status(
        db: AsyncSession,
        session_id: uuid.UUID,
        *,
        status: str,
        summary: str | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if summary is not None:
            values["summary"] = summary
        if ended_at is not None:
            values["ended_at"] = ended_at

        stmt = update(Session).where(Session.id == session_id).values(**values)
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
    async def list_pending(
        db: AsyncSession,
        *,
        session_id: uuid.UUID | None = None,
    ) -> Sequence[ApprovalRequest]:
        stmt = select(ApprovalRequest).where(ApprovalRequest.status == "pending")
        if session_id is not None:
            stmt = stmt.where(ApprovalRequest.session_id == session_id)
        stmt = stmt.order_by(ApprovalRequest.requested_at)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list(
        db: AsyncSession,
        *,
        status: str | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ApprovalRequest]:
        stmt = select(ApprovalRequest).order_by(ApprovalRequest.requested_at.desc())
        if status is not None:
            stmt = stmt.where(ApprovalRequest.status == status)
        if session_id is not None:
            stmt = stmt.where(ApprovalRequest.session_id == session_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def resolve(
        db: AsyncSession,
        request_id: uuid.UUID,
        *,
        status: str,
        resolved_by: uuid.UUID | None = None,
    ) -> bool:
        stmt = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == request_id,
                ApprovalRequest.status == "pending",
            )
            .values(
                status=status,
                resolved_at=datetime.now(timezone.utc),
                resolved_by=resolved_by,
            )
        )
        result = await db.execute(stmt)
        return bool(result.rowcount)


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
        api_version: str | None = None,
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
            api_version=api_version,
            max_tokens=max_tokens,
            temperature=temperature,
            is_default=is_default,
        )
        db.add(cfg)
        await db.flush()
        return cfg

    @staticmethod
    async def get_by_id(db: AsyncSession, config_id: uuid.UUID) -> ModelConfig | None:
        return await db.get(ModelConfig, config_id)

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> ModelConfig | None:
        stmt = select(ModelConfig).where(ModelConfig.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

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
    async def update(
        db: AsyncSession,
        config_id: uuid.UUID,
        *,
        name: str,
        provider: str,
        model_id: str,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelConfig | None:
        stmt = (
            update(ModelConfig)
            .where(ModelConfig.id == config_id)
            .values(
                name=name,
                provider=provider,
                model_id=model_id,
                api_key_env_var=api_key_env_var,
                base_url=base_url,
                api_version=api_version,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await ModelConfigRepo.get_by_id(db, config_id)

    @staticmethod
    async def delete(db: AsyncSession, config_id: uuid.UUID) -> bool:
        cfg = await ModelConfigRepo.get_by_id(db, config_id)
        if cfg is None:
            return False
        await db.delete(cfg)
        await db.flush()
        return True

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

    @staticmethod
    async def upsert(
        db: AsyncSession,
        *,
        name: str,
        provider: str,
        model_id: str,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        is_default: bool = False,
    ) -> ModelConfig:
        existing = await ModelConfigRepo.get_by_name(db, name)
        if existing is None:
            return await ModelConfigRepo.create(
                db,
                name=name,
                provider=provider,
                model_id=model_id,
                api_key_env_var=api_key_env_var,
                base_url=base_url,
                api_version=api_version,
                max_tokens=max_tokens,
                temperature=temperature,
                is_default=is_default,
            )

        stmt = (
            update(ModelConfig)
            .where(ModelConfig.id == existing.id)
            .values(
                provider=provider,
                model_id=model_id,
                api_key_env_var=api_key_env_var,
                base_url=base_url,
                api_version=api_version,
                max_tokens=max_tokens,
                temperature=temperature,
                is_default=is_default,
            )
        )
        await db.execute(stmt)
        await db.flush()
        refreshed = await ModelConfigRepo.get_by_id(db, existing.id)
        if refreshed is None:
            raise RuntimeError(f"ModelConfig disappeared during upsert: {existing.id}")
        return refreshed


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

class MCPServerRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        token: str | None = None,
        env_vars: dict[str, str] | None = None,
        is_active: bool = True,
    ) -> MCPServer:
        server = MCPServer(
            name=name,
            transport=transport,
            command=command,
            args=args,
            url=url,
            token=token,
            env_vars=env_vars,
            is_active=is_active,
        )
        db.add(server)
        await db.flush()
        return server

    @staticmethod
    async def get_by_id(db: AsyncSession, server_id: uuid.UUID) -> MCPServer | None:
        return await db.get(MCPServer, server_id)

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> MCPServer | None:
        stmt = select(MCPServer).where(MCPServer.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, *, active_only: bool = False
    ) -> Sequence[MCPServer]:
        stmt = select(MCPServer).order_by(MCPServer.name)
        if active_only:
            stmt = stmt.where(MCPServer.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        server_id: uuid.UUID,
        *,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        token: str | None = None,
        env_vars: dict[str, str] | None = None,
        is_active: bool = True,
    ) -> MCPServer | None:
        stmt = (
            update(MCPServer)
            .where(MCPServer.id == server_id)
            .values(
                name=name,
                transport=transport,
                command=command,
                args=args,
                url=url,
                token=token,
                env_vars=env_vars,
                is_active=is_active,
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await MCPServerRepo.get_by_id(db, server_id)

    @staticmethod
    async def delete(db: AsyncSession, server_id: uuid.UUID) -> bool:
        server = await MCPServerRepo.get_by_id(db, server_id)
        if server is None:
            return False
        await db.delete(server)
        await db.flush()
        return True


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

class SkillRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        content_md: str,
        description: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
    ) -> Skill:
        skill = Skill(
            name=name,
            description=description,
            mcp_server_id=mcp_server_id,
            content_md=content_md,
        )
        db.add(skill)
        await db.flush()
        return skill

    @staticmethod
    async def get_by_id(db: AsyncSession, skill_id: uuid.UUID) -> Skill | None:
        return await db.get(Skill, skill_id)

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> Skill | None:
        stmt = select(Skill).where(Skill.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[Skill]:
        stmt = select(Skill).order_by(Skill.name)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_for_mcp_server(
        db: AsyncSession, mcp_server_id: uuid.UUID
    ) -> Sequence[Skill]:
        stmt = (
            select(Skill)
            .where(Skill.mcp_server_id == mcp_server_id)
            .order_by(Skill.name)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_for_mcp_server(
        db: AsyncSession, mcp_server_id: uuid.UUID | None
    ) -> Skill | None:
        """Return the most relevant skill for a given MCP server.

        Falls back to a global skill (``mcp_server_id IS NULL``) when no
        server-specific skill exists. Returns ``None`` if no skill matches.
        """
        if mcp_server_id is not None:
            stmt = (
                select(Skill)
                .where(Skill.mcp_server_id == mcp_server_id)
                .order_by(Skill.created_at)
                .limit(1)
            )
            result = await db.execute(stmt)
            found = result.scalar_one_or_none()
            if found is not None:
                return found

        stmt = (
            select(Skill)
            .where(Skill.mcp_server_id.is_(None))
            .order_by(Skill.created_at)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        db: AsyncSession,
        skill_id: uuid.UUID,
        *,
        name: str,
        content_md: str,
        description: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
    ) -> Skill | None:
        stmt = (
            update(Skill)
            .where(Skill.id == skill_id)
            .values(
                name=name,
                content_md=content_md,
                description=description,
                mcp_server_id=mcp_server_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await SkillRepo.get_by_id(db, skill_id)

    @staticmethod
    async def delete(db: AsyncSession, skill_id: uuid.UUID) -> bool:
        skill = await SkillRepo.get_by_id(db, skill_id)
        if skill is None:
            return False
        await db.delete(skill)
        await db.flush()
        return True


# ---------------------------------------------------------------------------
# Session messages (co-pilot chat)
# ---------------------------------------------------------------------------

class SessionMessageRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        consumed_by_workflow: bool = False,
        node_context: str | None = None,
    ) -> SessionMessage:
        message = SessionMessage(
            session_id=session_id,
            role=role,
            content=content,
            consumed_by_workflow=consumed_by_workflow,
            node_context=node_context,
        )
        db.add(message)
        await db.flush()
        return message

    @staticmethod
    async def get_by_id(
        db: AsyncSession, message_id: uuid.UUID
    ) -> SessionMessage | None:
        return await db.get(SessionMessage, message_id)

    @staticmethod
    async def list_by_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> Sequence[SessionMessage]:
        stmt = (
            select(SessionMessage)
            .where(SessionMessage.session_id == session_id)
            .order_by(SessionMessage.created_at)
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_pending_user(
        db: AsyncSession, session_id: uuid.UUID
    ) -> Sequence[SessionMessage]:
        """Unread user messages that the workflow has not yet consumed."""
        stmt = (
            select(SessionMessage)
            .where(
                SessionMessage.session_id == session_id,
                SessionMessage.role == "user",
                SessionMessage.consumed_by_workflow.is_(False),
            )
            .order_by(SessionMessage.created_at)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def mark_consumed(
        db: AsyncSession,
        session_id: uuid.UUID,
        *,
        node_context: str | None = None,
    ) -> int:
        """Mark all unread user messages for a session as consumed.

        Returns the number of messages updated.
        """
        values: dict[str, Any] = {"consumed_by_workflow": True}
        if node_context is not None:
            values["node_context"] = node_context
        stmt = (
            update(SessionMessage)
            .where(
                SessionMessage.session_id == session_id,
                SessionMessage.role == "user",
                SessionMessage.consumed_by_workflow.is_(False),
            )
            .values(**values)
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------

class RuntimeConfigRepo:

    @staticmethod
    async def get(db: AsyncSession, key: str) -> RuntimeConfig | None:
        return await db.get(RuntimeConfig, key)

    @staticmethod
    async def get_value(db: AsyncSession, key: str) -> str | None:
        item = await RuntimeConfigRepo.get(db, key)
        return None if item is None else item.value

    @staticmethod
    async def get_many(
        db: AsyncSession, keys: Sequence[str]
    ) -> dict[str, str]:
        stmt = select(RuntimeConfig).where(RuntimeConfig.key.in_(list(keys)))
        result = await db.execute(stmt)
        return {item.key: item.value for item in result.scalars().all()}

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[RuntimeConfig]:
        stmt = select(RuntimeConfig).order_by(RuntimeConfig.key)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def set(db: AsyncSession, *, key: str, value: str) -> RuntimeConfig:
        item = await RuntimeConfigRepo.get(db, key)
        if item is None:
            item = RuntimeConfig(key=key, value=value)
            db.add(item)
            await db.flush()
            return item

        item.value = value
        item.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return item


# ---------------------------------------------------------------------------
# Ingest tokens (Sprint 14)
# ---------------------------------------------------------------------------

class IngestTokenRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        provider: str,
        token_hash: str,
    ) -> IngestToken:
        token = IngestToken(
            name=name,
            provider=provider,
            token_hash=token_hash,
        )
        db.add(token)
        await db.flush()
        return token

    @staticmethod
    async def get_by_id(db: AsyncSession, token_id: uuid.UUID) -> IngestToken | None:
        return await db.get(IngestToken, token_id)

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> IngestToken | None:
        stmt = select(IngestToken).where(IngestToken.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, *, active_only: bool = False
    ) -> Sequence[IngestToken]:
        stmt = select(IngestToken).order_by(IngestToken.created_at.desc())
        if active_only:
            stmt = stmt.where(IngestToken.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def revoke(db: AsyncSession, token_id: uuid.UUID) -> bool:
        """Deactivate a token (soft-delete)."""
        stmt = (
            update(IngestToken)
            .where(IngestToken.id == token_id)
            .values(is_active=False)
        )
        result = await db.execute(stmt)
        return bool(result.rowcount)

    @staticmethod
    async def touch(db: AsyncSession, token_id: uuid.UUID) -> None:
        """Update last_used_at to now."""
        stmt = (
            update(IngestToken)
            .where(IngestToken.id == token_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)

    @staticmethod
    async def delete(db: AsyncSession, token_id: uuid.UUID) -> bool:
        tok = await IngestTokenRepo.get_by_id(db, token_id)
        if tok is None:
            return False
        await db.delete(tok)
        await db.flush()
        return True


# ---------------------------------------------------------------------------
# Ingest log (Sprint 14)
# ---------------------------------------------------------------------------

class IngestLogRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        ingest_token_id: uuid.UUID,
        provider: str,
        raw_payload: dict,
        incident_id: uuid.UUID | None = None,
        dedup_action: str | None = None,
        error: str | None = None,
    ) -> IngestLog:
        entry = IngestLog(
            ingest_token_id=ingest_token_id,
            provider=provider,
            raw_payload=raw_payload,
            incident_id=incident_id,
            dedup_action=dedup_action,
            error=error,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def list_recent(
        db: AsyncSession,
        *,
        limit: int = 50,
        offset: int = 0,
        token_id: uuid.UUID | None = None,
    ) -> Sequence[IngestLog]:
        stmt = select(IngestLog).order_by(IngestLog.created_at.desc())
        if token_id is not None:
            stmt = stmt.where(IngestLog.ingest_token_id == token_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Detector rules / history (MCP-driven incident detection — Sprint 14)
# ---------------------------------------------------------------------------

class DetectorRuleRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        mcp_server_id: uuid.UUID,
        prompt_template: str,
        model_config_id: uuid.UUID | None = None,
        interval_seconds: int = 300,
        severity_default: str = "medium",
        is_active: bool = True,
    ) -> DetectorRule:
        rule = DetectorRule(
            name=name,
            mcp_server_id=mcp_server_id,
            prompt_template=prompt_template,
            model_config_id=model_config_id,
            interval_seconds=interval_seconds,
            severity_default=severity_default,
            is_active=is_active,
        )
        db.add(rule)
        await db.flush()
        return rule

    @staticmethod
    async def get_by_id(db: AsyncSession, rule_id: uuid.UUID) -> DetectorRule | None:
        return await db.get(DetectorRule, rule_id)

    @staticmethod
    async def get_by_name(db: AsyncSession, name: str) -> DetectorRule | None:
        stmt = select(DetectorRule).where(DetectorRule.name == name)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        *,
        active_only: bool = False,
    ) -> Sequence[DetectorRule]:
        stmt = select(DetectorRule).order_by(DetectorRule.name)
        if active_only:
            stmt = stmt.where(DetectorRule.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        rule_id: uuid.UUID,
        *,
        name: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
        prompt_template: str | None = None,
        model_config_id: uuid.UUID | None = None,
        model_config_id_provided: bool = False,
        interval_seconds: int | None = None,
        severity_default: str | None = None,
        is_active: bool | None = None,
    ) -> DetectorRule | None:
        values: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc),
        }
        if name is not None:
            values["name"] = name
        if mcp_server_id is not None:
            values["mcp_server_id"] = mcp_server_id
        if prompt_template is not None:
            values["prompt_template"] = prompt_template
        if model_config_id_provided:
            values["model_config_id"] = model_config_id
        if interval_seconds is not None:
            values["interval_seconds"] = interval_seconds
        if severity_default is not None:
            values["severity_default"] = severity_default
        if is_active is not None:
            values["is_active"] = is_active

        stmt = update(DetectorRule).where(DetectorRule.id == rule_id).values(**values)
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await DetectorRuleRepo.get_by_id(db, rule_id)

    @staticmethod
    async def mark_run(
        db: AsyncSession,
        rule_id: uuid.UUID,
        *,
        last_ran_at: datetime | None = None,
        last_fingerprint: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc),
            "last_ran_at": last_ran_at or datetime.now(timezone.utc),
        }
        if last_fingerprint is not None:
            values["last_fingerprint"] = last_fingerprint
        stmt = update(DetectorRule).where(DetectorRule.id == rule_id).values(**values)
        await db.execute(stmt)

    @staticmethod
    async def delete(db: AsyncSession, rule_id: uuid.UUID) -> bool:
        rule = await DetectorRuleRepo.get_by_id(db, rule_id)
        if rule is None:
            return False
        await db.delete(rule)
        await db.flush()
        return True


class DetectorHistoryRepo:

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        rule_id: uuid.UUID,
        duration_ms: int | None = None,
        issue_detected: bool = False,
        incident_id: uuid.UUID | None = None,
        raw_verdict: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> DetectorHistory:
        row = DetectorHistory(
            rule_id=rule_id,
            duration_ms=duration_ms,
            issue_detected=issue_detected,
            incident_id=incident_id,
            raw_verdict=raw_verdict,
            error=error,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_by_rule(
        db: AsyncSession,
        rule_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DetectorHistory]:
        stmt = (
            select(DetectorHistory)
            .where(DetectorHistory.rule_id == rule_id)
            .order_by(DetectorHistory.ran_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()
