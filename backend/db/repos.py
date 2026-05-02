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
    AgentTeamProfile,
    ApprovalRequest,
    AuditEntry,
    BotActionAudit,
    BotConnector,
    BotUserLink,
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
    WebhookTrigger,
    WorkflowProfile,
    SLATarget,
    UptimeSample,
    SLO,
    MaintenanceWindow,
    Organization,
    UserOrganization,
)


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
            username=username, email=email, password_hash=password_hash, role=role
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
    async def list_all(
        db: AsyncSession, *, limit: int = 100, offset: int = 0
    ) -> Sequence[User]:
        stmt = select(User).order_by(User.created_at).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()


class IncidentRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        title: str,
        description: str,
        severity: str | None = None,
    ) -> Incident:
        incident = Incident(
            org_id=org_id, title=title, description=description, severity=severity
        )
        db.add(incident)
        await db.flush()
        return incident

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Incident | None:
        return (
            await db.execute(
                select(Incident)
                .where(Incident.org_id == org_id)
                .where(Incident.org_id == org_id)
                .where(Incident.org_id == org_id)
                .where(Incident.id == incident_id, Incident.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .order_by(Incident.created_at.desc())
        )
        if status:
            stmt = stmt.where(Incident.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_status(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID, status: str
    ) -> None:
        stmt = (
            update(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.id == incident_id)
            .values(status=status, updated_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)

    @staticmethod
    async def get_by_external_fingerprint(
        db: AsyncSession, org_id: uuid.UUID, *, external_source: str, external_id: str
    ) -> Incident | None:
        """Look up an incident by its external fingerprint for dedup."""
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(
                Incident.external_source == external_source,
                Incident.external_id == external_id,
            )
            .order_by(Incident.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_target(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        """List incidents linked to an SLA target."""
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.org_id == org_id)
            .where(Incident.target_id == target_id)
            .order_by(Incident.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


class SessionRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        tier: int,
        incident_id: uuid.UUID | None = None,
        workflow_profile_id: uuid.UUID | None = None,
        agent_team_profile_id: uuid.UUID | None = None,
        model_provider: str | None = None,
        model_id: str | None = None,
    ) -> Session:
        session = Session(
            org_id=org_id,
            tier=tier,
            incident_id=incident_id,
            workflow_profile_id=workflow_profile_id,
            agent_team_profile_id=agent_team_profile_id,
            model_provider=model_provider,
            model_id=model_id,
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID
    ) -> Session | None:
        return (
            await db.execute(
                select(Session)
                .where(Session.org_id == org_id)
                .where(Session.org_id == org_id)
                .where(Session.org_id == org_id)
                .where(Session.id == session_id, Session.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_by_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[Session]:
        stmt = (
            select(Session)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.incident_id == incident_id)
            .order_by(Session.started_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Session]:
        stmt = (
            select(Session)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .order_by(Session.started_at.desc())
        )
        if status is not None:
            stmt = stmt.where(Session.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def end_session(
        db: AsyncSession,
        org_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        status: str = "completed",
        summary: str | None = None,
    ) -> None:
        stmt = (
            update(Session)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.id == session_id)
            .values(status=status, summary=summary, ended_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)

    @staticmethod
    async def set_status(
        db: AsyncSession,
        org_id: uuid.UUID,
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
        stmt = (
            update(Session)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.org_id == org_id)
            .where(Session.id == session_id)
            .values(**values)
        )
        await db.execute(stmt)


class AuditEntryRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            org_id=org_id,
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
        db: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID
    ) -> Sequence[AuditEntry]:
        stmt = (
            select(AuditEntry)
            .where(AuditEntry.org_id == org_id)
            .where(AuditEntry.org_id == org_id)
            .where(AuditEntry.org_id == org_id)
            .where(AuditEntry.session_id == session_id)
            .order_by(AuditEntry.timestamp)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def query(
        db: AsyncSession,
        org_id: uuid.UUID,
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
        stmt = (
            select(AuditEntry)
            .where(AuditEntry.org_id == org_id)
            .where(AuditEntry.org_id == org_id)
            .where(AuditEntry.org_id == org_id)
            .order_by(AuditEntry.timestamp.desc())
        )
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


class ApprovalRequestRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        session_id: uuid.UUID,
        action: dict[str, Any],
        justification: str | None = None,
        expires_at: datetime,
    ) -> ApprovalRequest:
        req = ApprovalRequest(
            org_id=org_id,
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
        db: AsyncSession, org_id: uuid.UUID, request_id: uuid.UUID
    ) -> ApprovalRequest | None:
        return (
            await db.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.org_id == org_id)
                .where(ApprovalRequest.org_id == org_id)
                .where(ApprovalRequest.org_id == org_id)
                .where(
                    ApprovalRequest.id == request_id, ApprovalRequest.org_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_pending(
        db: AsyncSession, org_id: uuid.UUID, *, session_id: uuid.UUID | None = None
    ) -> Sequence[ApprovalRequest]:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.status == "pending")
        )
        if session_id is not None:
            stmt = stmt.where(ApprovalRequest.session_id == session_id)
        stmt = stmt.order_by(ApprovalRequest.requested_at)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        status: str | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ApprovalRequest]:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .order_by(ApprovalRequest.requested_at.desc())
        )
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
        org_id: uuid.UUID,
        request_id: uuid.UUID,
        *,
        status: str,
        resolved_by: uuid.UUID | None = None,
    ) -> bool:
        stmt = (
            update(ApprovalRequest)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(
                ApprovalRequest.id == request_id, ApprovalRequest.status == "pending"
            )
            .values(
                status=status,
                resolved_at=datetime.now(timezone.utc),
                resolved_by=resolved_by,
            )
        )
        result = await db.execute(stmt)
        return bool(result.rowcount)


class ModelConfigRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            org_id=org_id,
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
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, config_id: uuid.UUID
    ) -> ModelConfig | None:
        return (
            await db.execute(
                select(ModelConfig)
                .where(ModelConfig.org_id == org_id)
                .where(ModelConfig.org_id == org_id)
                .where(ModelConfig.org_id == org_id)
                .where(ModelConfig.id == config_id, ModelConfig.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> ModelConfig | None:
        stmt = (
            select(ModelConfig)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_default(db: AsyncSession, org_id: uuid.UUID) -> ModelConfig | None:
        stmt = (
            select(ModelConfig)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.is_default == True)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession, org_id: uuid.UUID) -> Sequence[ModelConfig]:
        stmt = (
            select(ModelConfig)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .order_by(ModelConfig.name)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
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
        return await ModelConfigRepo.get_by_id(db, org_id, config_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, config_id: uuid.UUID) -> bool:
        cfg = await ModelConfigRepo.get_by_id(db, org_id, config_id)
        if cfg is None:
            return False
        await db.delete(cfg)
        await db.flush()
        return True

    @staticmethod
    async def set_default(
        db: AsyncSession, org_id: uuid.UUID, config_id: uuid.UUID
    ) -> None:
        await db.execute(
            update(ModelConfig)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .values(is_default=False)
        )
        await db.execute(
            update(ModelConfig)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.id == config_id)
            .values(is_default=True)
        )

    @staticmethod
    async def upsert(
        db: AsyncSession,
        org_id: uuid.UUID,
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
        existing = await ModelConfigRepo.get_by_name(db, org_id, name)
        if existing is None:
            return await ModelConfigRepo.create(
                db,
                org_id,
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
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
            .where(ModelConfig.org_id == org_id)
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
        refreshed = await ModelConfigRepo.get_by_id(db, org_id, existing.id)
        if refreshed is None:
            raise RuntimeError(f"ModelConfig disappeared during upsert: {existing.id}")
        return refreshed


class MCPServerRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            org_id=org_id,
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
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, server_id: uuid.UUID
    ) -> MCPServer | None:
        return (
            await db.execute(
                select(MCPServer)
                .where(MCPServer.org_id == org_id)
                .where(MCPServer.org_id == org_id)
                .where(MCPServer.org_id == org_id)
                .where(MCPServer.id == server_id, MCPServer.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> MCPServer | None:
        stmt = (
            select(MCPServer)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[MCPServer]:
        stmt = (
            select(MCPServer)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
            .order_by(MCPServer.name)
        )
        if active_only:
            stmt = stmt.where(MCPServer.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.org_id == org_id)
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
        return await MCPServerRepo.get_by_id(db, org_id, server_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, server_id: uuid.UUID) -> bool:
        server = await MCPServerRepo.get_by_id(db, org_id, server_id)
        if server is None:
            return False
        await db.delete(server)
        await db.flush()
        return True


class SkillRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        content_md: str,
        description: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
    ) -> Skill:
        skill = Skill(
            org_id=org_id,
            name=name,
            description=description,
            mcp_server_id=mcp_server_id,
            content_md=content_md,
        )
        db.add(skill)
        await db.flush()
        return skill

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, skill_id: uuid.UUID
    ) -> Skill | None:
        return (
            await db.execute(
                select(Skill)
                .where(Skill.org_id == org_id)
                .where(Skill.org_id == org_id)
                .where(Skill.org_id == org_id)
                .where(Skill.id == skill_id, Skill.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> Skill | None:
        stmt = (
            select(Skill)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession, org_id: uuid.UUID) -> Sequence[Skill]:
        stmt = (
            select(Skill)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .order_by(Skill.name)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_for_mcp_server(
        db: AsyncSession, org_id: uuid.UUID, mcp_server_id: uuid.UUID
    ) -> Sequence[Skill]:
        stmt = (
            select(Skill)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.mcp_server_id == mcp_server_id)
            .order_by(Skill.name)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_for_mcp_server(
        db: AsyncSession, org_id: uuid.UUID, mcp_server_id: uuid.UUID | None
    ) -> Skill | None:
        """Return the most relevant skill for a given MCP server.

        Falls back to a global skill (``mcp_server_id IS NULL``) when no
        server-specific skill exists. Returns ``None`` if no skill matches.
        """
        if mcp_server_id is not None:
            stmt = (
                select(Skill)
                .where(Skill.org_id == org_id)
                .where(Skill.org_id == org_id)
                .where(Skill.org_id == org_id)
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
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.mcp_server_id.is_(None))
            .order_by(Skill.created_at)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        skill_id: uuid.UUID,
        *,
        name: str,
        content_md: str,
        description: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
    ) -> Skill | None:
        stmt = (
            update(Skill)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
            .where(Skill.org_id == org_id)
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
        return await SkillRepo.get_by_id(db, org_id, skill_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        skill = await SkillRepo.get_by_id(db, org_id, skill_id)
        if skill is None:
            return False
        await db.delete(skill)
        await db.flush()
        return True


class SessionMessageRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        consumed_by_workflow: bool = False,
        node_context: str | None = None,
    ) -> SessionMessage:
        message = SessionMessage(
            org_id=org_id,
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
        db: AsyncSession, org_id: uuid.UUID, message_id: uuid.UUID
    ) -> SessionMessage | None:
        return (
            await db.execute(
                select(SessionMessage)
                .where(SessionMessage.org_id == org_id)
                .where(SessionMessage.org_id == org_id)
                .where(SessionMessage.org_id == org_id)
                .where(SessionMessage.id == message_id, SessionMessage.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_by_session(
        db: AsyncSession,
        org_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> Sequence[SessionMessage]:
        stmt = (
            select(SessionMessage)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.session_id == session_id)
            .order_by(SessionMessage.created_at)
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_pending_user(
        db: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID
    ) -> Sequence[SessionMessage]:
        """Unread user messages that the workflow has not yet consumed."""
        stmt = (
            select(SessionMessage)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
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
        org_id: uuid.UUID,
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
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
            .where(SessionMessage.org_id == org_id)
            .where(
                SessionMessage.session_id == session_id,
                SessionMessage.role == "user",
                SessionMessage.consumed_by_workflow.is_(False),
            )
            .values(**values)
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)


class RuntimeConfigRepo:
    @staticmethod
    async def get(
        db: AsyncSession, org_id: uuid.UUID, key: str
    ) -> RuntimeConfig | None:
        return (
            await db.execute(
                select(RuntimeConfig).where(
                    RuntimeConfig.org_id == org_id, RuntimeConfig.key == key
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_value(db: AsyncSession, org_id: uuid.UUID, key: str) -> str | None:
        item = await RuntimeConfigRepo.get(db, org_id, key)
        return None if item is None else item.value

    @staticmethod
    async def get_many(
        db: AsyncSession, org_id: uuid.UUID, keys: Sequence[str]
    ) -> dict[str, str]:
        stmt = (
            select(RuntimeConfig)
            .where(RuntimeConfig.org_id == org_id)
            .where(RuntimeConfig.org_id == org_id)
            .where(RuntimeConfig.org_id == org_id)
            .where(RuntimeConfig.key.in_(list(keys)))
        )
        result = await db.execute(stmt)
        return {item.key: item.value for item in result.scalars().all()}

    @staticmethod
    async def list_all(db: AsyncSession, org_id: uuid.UUID) -> Sequence[RuntimeConfig]:
        stmt = (
            select(RuntimeConfig)
            .where(RuntimeConfig.org_id == org_id)
            .where(RuntimeConfig.org_id == org_id)
            .where(RuntimeConfig.org_id == org_id)
            .order_by(RuntimeConfig.key)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def set(
        db: AsyncSession, org_id: uuid.UUID, *, key: str, value: str
    ) -> RuntimeConfig:
        item = await RuntimeConfigRepo.get(db, org_id, key)
        if item is None:
            item = RuntimeConfig(org_id=org_id, key=key, value=value)
            db.add(item)
            await db.flush()
            return item
        item.value = value
        item.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return item


class WebhookTriggerRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        url: str,
        format: str = "generic",
        event_types: list[str],
        headers: dict[str, str] | None = None,
        token: str | None = None,
        is_active: bool = True,
    ) -> WebhookTrigger:
        trigger = WebhookTrigger(
            org_id=org_id,
            name=name,
            url=url,
            format=format,
            event_types=event_types,
            headers=headers,
            token=token,
            is_active=is_active,
        )
        db.add(trigger)
        await db.flush()
        return trigger

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> WebhookTrigger | None:
        return (
            await db.execute(
                select(WebhookTrigger)
                .where(WebhookTrigger.org_id == org_id)
                .where(WebhookTrigger.org_id == org_id)
                .where(WebhookTrigger.org_id == org_id)
                .where(WebhookTrigger.id == trigger_id, WebhookTrigger.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> WebhookTrigger | None:
        stmt = (
            select(WebhookTrigger)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[WebhookTrigger]:
        stmt = (
            select(WebhookTrigger)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .order_by(WebhookTrigger.name)
        )
        if active_only:
            stmt = stmt.where(WebhookTrigger.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_matching_event(
        db: AsyncSession, org_id: uuid.UUID, event_type: str
    ) -> Sequence[WebhookTrigger]:
        items = await WebhookTriggerRepo.list_all(db, org_id, active_only=True)
        return [
            item
            for item in items
            if "*" in (item.event_types or []) or event_type in (item.event_types or [])
        ]

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        trigger_id: uuid.UUID,
        *,
        name: str,
        url: str,
        format: str = "generic",
        event_types: list[str],
        headers: dict[str, str] | None = None,
        token: str | None = None,
        is_active: bool = True,
    ) -> WebhookTrigger | None:
        stmt = (
            update(WebhookTrigger)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.id == trigger_id)
            .values(
                name=name,
                url=url,
                format=format,
                event_types=event_types,
                headers=headers,
                token=token,
                is_active=is_active,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await WebhookTriggerRepo.get_by_id(db, org_id, trigger_id)

    @staticmethod
    async def mark_delivery(
        db: AsyncSession,
        org_id: uuid.UUID,
        trigger_id: uuid.UUID,
        *,
        error: str | None = None,
    ) -> None:
        stmt = (
            update(WebhookTrigger)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.org_id == org_id)
            .where(WebhookTrigger.id == trigger_id)
            .values(
                last_triggered_at=datetime.now(timezone.utc),
                last_error=error,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(stmt)

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, trigger_id: uuid.UUID
    ) -> bool:
        trigger = await WebhookTriggerRepo.get_by_id(db, org_id, trigger_id)
        if trigger is None:
            return False
        await db.delete(trigger)
        await db.flush()
        return True


class WorkflowProfileRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        node_order: list[str],
        is_active: bool = True,
        is_default: bool = False,
    ) -> WorkflowProfile:
        if is_default:
            await db.execute(
                update(WorkflowProfile)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .values(is_default=False)
            )
        profile = WorkflowProfile(
            org_id=org_id,
            name=name,
            description=description,
            node_order=node_order,
            is_active=is_active,
            is_default=is_default,
        )
        db.add(profile)
        await db.flush()
        return profile

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, profile_id: uuid.UUID
    ) -> WorkflowProfile | None:
        return (
            await db.execute(
                select(WorkflowProfile)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .where(
                    WorkflowProfile.id == profile_id, WorkflowProfile.org_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> WorkflowProfile | None:
        stmt = (
            select(WorkflowProfile)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_default(
        db: AsyncSession, org_id: uuid.UUID
    ) -> WorkflowProfile | None:
        stmt = (
            select(WorkflowProfile)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.is_default == True)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[WorkflowProfile]:
        stmt = (
            select(WorkflowProfile)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .order_by(WorkflowProfile.is_default.desc(), WorkflowProfile.name)
        )
        if active_only:
            stmt = stmt.where(WorkflowProfile.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        node_order: list[str],
        is_active: bool,
        is_default: bool,
    ) -> WorkflowProfile | None:
        if is_default:
            await db.execute(
                update(WorkflowProfile)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.org_id == org_id)
                .where(WorkflowProfile.id != profile_id)
                .values(is_default=False)
            )
        stmt = (
            update(WorkflowProfile)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.org_id == org_id)
            .where(WorkflowProfile.id == profile_id)
            .values(
                name=name,
                description=description,
                node_order=node_order,
                is_active=is_active,
                is_default=is_default,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await WorkflowProfileRepo.get_by_id(db, org_id, profile_id)

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, profile_id: uuid.UUID
    ) -> bool:
        profile = await WorkflowProfileRepo.get_by_id(db, org_id, profile_id)
        if profile is None:
            return False
        await db.delete(profile)
        await db.flush()
        return True


class AgentTeamProfileRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        roles: list[str],
        is_active: bool = True,
        is_default: bool = False,
    ) -> AgentTeamProfile:
        if is_default:
            await db.execute(
                update(AgentTeamProfile)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .values(is_default=False)
            )
        profile = AgentTeamProfile(
            org_id=org_id,
            name=name,
            description=description,
            roles=roles,
            is_active=is_active,
            is_default=is_default,
        )
        db.add(profile)
        await db.flush()
        return profile

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, profile_id: uuid.UUID
    ) -> AgentTeamProfile | None:
        return (
            await db.execute(
                select(AgentTeamProfile)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .where(
                    AgentTeamProfile.id == profile_id, AgentTeamProfile.org_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> AgentTeamProfile | None:
        stmt = (
            select(AgentTeamProfile)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_default(
        db: AsyncSession, org_id: uuid.UUID
    ) -> AgentTeamProfile | None:
        stmt = (
            select(AgentTeamProfile)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.is_default == True)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[AgentTeamProfile]:
        stmt = (
            select(AgentTeamProfile)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .order_by(AgentTeamProfile.is_default.desc(), AgentTeamProfile.name)
        )
        if active_only:
            stmt = stmt.where(AgentTeamProfile.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        profile_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        roles: list[str],
        is_active: bool,
        is_default: bool,
    ) -> AgentTeamProfile | None:
        if is_default:
            await db.execute(
                update(AgentTeamProfile)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.org_id == org_id)
                .where(AgentTeamProfile.id != profile_id)
                .values(is_default=False)
            )
        stmt = (
            update(AgentTeamProfile)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.org_id == org_id)
            .where(AgentTeamProfile.id == profile_id)
            .values(
                name=name,
                description=description,
                roles=roles,
                is_active=is_active,
                is_default=is_default,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await AgentTeamProfileRepo.get_by_id(db, org_id, profile_id)

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, profile_id: uuid.UUID
    ) -> bool:
        profile = await AgentTeamProfileRepo.get_by_id(db, org_id, profile_id)
        if profile is None:
            return False
        await db.delete(profile)
        await db.flush()
        return True


class IngestTokenRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        provider: str,
        token_hash: str,
        shape_cache: dict | None = None,
    ) -> IngestToken:
        token = IngestToken(
            org_id=org_id,
            name=name,
            provider=provider,
            token_hash=token_hash,
            shape_cache=shape_cache,
        )
        db.add(token)
        await db.flush()
        return token

    @staticmethod
    async def update_shape_cache(
        db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID, shape_cache: dict
    ) -> bool:
        """Replace the full shape_cache dict for a token."""
        stmt = (
            update(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.id == token_id)
            .values(shape_cache=shape_cache)
        )
        result = await db.execute(stmt)
        return bool(result.rowcount)

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID
    ) -> IngestToken | None:
        return (
            await db.execute(
                select(IngestToken)
                .where(IngestToken.org_id == org_id)
                .where(IngestToken.org_id == org_id)
                .where(IngestToken.org_id == org_id)
                .where(IngestToken.id == token_id, IngestToken.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> IngestToken | None:
        stmt = (
            select(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[IngestToken]:
        stmt = (
            select(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .order_by(IngestToken.created_at.desc())
        )
        if active_only:
            stmt = stmt.where(IngestToken.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def revoke(db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID) -> bool:
        """Deactivate a token (soft-delete)."""
        stmt = (
            update(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.id == token_id)
            .values(is_active=False)
        )
        result = await db.execute(stmt)
        return bool(result.rowcount)

    @staticmethod
    async def touch(db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID) -> None:
        """Update last_used_at to now."""
        stmt = (
            update(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.id == token_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID) -> bool:
        tok = await IngestTokenRepo.get_by_id(db, org_id, token_id)
        if tok is None:
            return False
        await db.delete(tok)
        await db.flush()
        return True


class IngestLogRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        ingest_token_id: uuid.UUID,
        provider: str,
        raw_payload: dict,
        incident_id: uuid.UUID | None = None,
        dedup_action: str | None = None,
        error: str | None = None,
    ) -> IngestLog:
        entry = IngestLog(
            org_id=org_id,
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
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        token_id: uuid.UUID | None = None,
    ) -> Sequence[IngestLog]:
        stmt = (
            select(IngestLog)
            .where(IngestLog.org_id == org_id)
            .where(IngestLog.org_id == org_id)
            .where(IngestLog.org_id == org_id)
            .order_by(IngestLog.created_at.desc())
        )
        if token_id is not None:
            stmt = stmt.where(IngestLog.ingest_token_id == token_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()


class DetectorRuleRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
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
            org_id=org_id,
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
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, rule_id: uuid.UUID
    ) -> DetectorRule | None:
        return (
            await db.execute(
                select(DetectorRule)
                .where(DetectorRule.org_id == org_id)
                .where(DetectorRule.org_id == org_id)
                .where(DetectorRule.org_id == org_id)
                .where(DetectorRule.id == rule_id, DetectorRule.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> DetectorRule | None:
        stmt = (
            select(DetectorRule)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[DetectorRule]:
        stmt = (
            select(DetectorRule)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .order_by(DetectorRule.name)
        )
        if active_only:
            stmt = stmt.where(DetectorRule.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
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
        values: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
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
        stmt = (
            update(DetectorRule)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.id == rule_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await DetectorRuleRepo.get_by_id(db, org_id, rule_id)

    @staticmethod
    async def mark_run(
        db: AsyncSession,
        org_id: uuid.UUID,
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
        stmt = (
            update(DetectorRule)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.org_id == org_id)
            .where(DetectorRule.id == rule_id)
            .values(**values)
        )
        await db.execute(stmt)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        rule = await DetectorRuleRepo.get_by_id(db, org_id, rule_id)
        if rule is None:
            return False
        await db.delete(rule)
        await db.flush()
        return True


class DetectorHistoryRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        rule_id: uuid.UUID,
        duration_ms: int | None = None,
        issue_detected: bool = False,
        incident_id: uuid.UUID | None = None,
        raw_verdict: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> DetectorHistory:
        row = DetectorHistory(
            org_id=org_id,
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
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[DetectorHistory]:
        stmt = (
            select(DetectorHistory)
            .where(DetectorHistory.org_id == org_id)
            .where(DetectorHistory.org_id == org_id)
            .where(DetectorHistory.org_id == org_id)
            .where(DetectorHistory.rule_id == rule_id)
            .order_by(DetectorHistory.ran_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


class SLATargetRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        kind: str,
        config: dict[str, Any] | None = None,
        owner_team: str | None = None,
        is_active: bool = True,
    ) -> SLATarget:
        target = SLATarget(
            org_id=org_id,
            name=name,
            kind=kind,
            config=config,
            owner_team=owner_team,
            is_active=is_active,
        )
        db.add(target)
        await db.flush()
        return target

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[SLATarget]:
        stmt = (
            select(SLATarget)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .order_by(SLATarget.created_at)
        )
        if active_only:
            stmt = stmt.where(SLATarget.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID
    ) -> SLATarget | None:
        return (
            await db.execute(
                select(SLATarget)
                .where(SLATarget.org_id == org_id)
                .where(SLATarget.org_id == org_id)
                .where(SLATarget.org_id == org_id)
                .where(SLATarget.id == target_id, SLATarget.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> SLATarget | None:
        stmt = (
            select(SLATarget)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        name: str | None = None,
        kind: str | None = None,
        config: dict[str, Any] | None = None,
        config_provided: bool = False,
        owner_team: str | None = None,
        owner_team_provided: bool = False,
        is_active: bool | None = None,
    ) -> SLATarget | None:
        values: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
        if name is not None:
            values["name"] = name
        if kind is not None:
            values["kind"] = kind
        if config_provided:
            values["config"] = config
        if owner_team_provided:
            values["owner_team"] = owner_team
        if is_active is not None:
            values["is_active"] = is_active
        stmt = (
            update(SLATarget)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.org_id == org_id)
            .where(SLATarget.id == target_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await SLATargetRepo.get_by_id(db, org_id, target_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID) -> bool:
        target = await SLATargetRepo.get_by_id(db, org_id, target_id)
        if target is None:
            return False
        await db.delete(target)
        await db.flush()
        return True


class UptimeSampleRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        target_id: uuid.UUID,
        up: bool,
        latency_ms: int | None = None,
        source: str = "poller",
        suppressed: bool = False,
    ) -> UptimeSample:
        sample = UptimeSample(
            org_id=org_id,
            target_id=target_id,
            up=up,
            latency_ms=latency_ms,
            source=source,
            suppressed=suppressed,
        )
        db.add(sample)
        await db.flush()
        return sample

    @staticmethod
    async def query_window(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> Sequence[UptimeSample]:
        """Return raw samples in [since, until] for a target."""
        until = until or datetime.now(timezone.utc)
        stmt = (
            select(UptimeSample)
            .where(UptimeSample.org_id == org_id)
            .where(UptimeSample.org_id == org_id)
            .where(UptimeSample.org_id == org_id)
            .where(
                UptimeSample.target_id == target_id,
                UptimeSample.observed_at >= since,
                UptimeSample.observed_at <= until,
            )
            .order_by(UptimeSample.observed_at)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def compute_uptime(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Compute aggregate uptime statistics for a target over a window.

        Returns a dict with: uptime_pct, total_samples, up_samples,
        downtime_seconds, suppressed_seconds.
        """
        until = until or datetime.now(timezone.utc)
        samples = await UptimeSampleRepo.query_window(
            db, org_id, target_id, since=since, until=until
        )
        total = len(samples)
        if total == 0:
            return {
                "uptime_pct": 100.0,
                "total_samples": 0,
                "up_samples": 0,
                "downtime_seconds": 0,
                "suppressed_seconds": 0,
            }
        non_suppressed = [s for s in samples if not s.suppressed]
        suppressed_count = total - len(non_suppressed)
        up_count = sum((1 for s in non_suppressed if s.up))
        ns_total = len(non_suppressed)
        uptime_pct = up_count / ns_total * 100.0 if ns_total > 0 else 100.0
        downtime_seconds = (ns_total - up_count) * 60
        suppressed_seconds = suppressed_count * 60
        return {
            "uptime_pct": round(uptime_pct, 4),
            "total_samples": total,
            "up_samples": up_count,
            "downtime_seconds": downtime_seconds,
            "suppressed_seconds": suppressed_seconds,
        }


class SLORepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        target_id: uuid.UUID,
        name: str,
        objective_pct: float,
        window_seconds: int,
        burn_alert_threshold: float | None = None,
        is_active: bool = True,
    ) -> SLO:
        slo = SLO(
            org_id=org_id,
            target_id=target_id,
            name=name,
            objective_pct=objective_pct,
            window_seconds=window_seconds,
            burn_alert_threshold=burn_alert_threshold,
            is_active=is_active,
        )
        db.add(slo)
        await db.flush()
        return slo

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, slo_id: uuid.UUID
    ) -> SLO | None:
        return (
            await db.execute(
                select(SLO)
                .where(SLO.org_id == org_id)
                .where(SLO.org_id == org_id)
                .where(SLO.org_id == org_id)
                .where(SLO.id == slo_id, SLO.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID, *, active_only: bool = False
    ) -> Sequence[SLO]:
        stmt = (
            select(SLO)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .order_by(SLO.created_at)
        )
        if active_only:
            stmt = stmt.where(SLO.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_by_target(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> Sequence[SLO]:
        stmt = (
            select(SLO)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .where(SLO.target_id == target_id)
            .order_by(SLO.created_at)
        )
        if active_only:
            stmt = stmt.where(SLO.is_active == True)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        slo_id: uuid.UUID,
        *,
        name: str | None = None,
        objective_pct: float | None = None,
        window_seconds: int | None = None,
        burn_alert_threshold: float | None = None,
        burn_alert_threshold_provided: bool = False,
        is_active: bool | None = None,
    ) -> SLO | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if objective_pct is not None:
            values["objective_pct"] = objective_pct
        if window_seconds is not None:
            values["window_seconds"] = window_seconds
        if burn_alert_threshold_provided:
            values["burn_alert_threshold"] = burn_alert_threshold
        if is_active is not None:
            values["is_active"] = is_active
        if not values:
            return await SLORepo.get_by_id(db, org_id, slo_id)
        stmt = (
            update(SLO)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .where(SLO.org_id == org_id)
            .where(SLO.id == slo_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await SLORepo.get_by_id(db, org_id, slo_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, slo_id: uuid.UUID) -> bool:
        slo = await SLORepo.get_by_id(db, org_id, slo_id)
        if slo is None:
            return False
        await db.delete(slo)
        await db.flush()
        return True


class MaintenanceWindowRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        reason: str | None = None,
        starts_at: datetime,
        ends_at: datetime,
        rrule: str | None = None,
        target_ids: list[str],
        created_by: uuid.UUID | None = None,
    ) -> MaintenanceWindow:
        mw = MaintenanceWindow(
            org_id=org_id,
            name=name,
            reason=reason,
            starts_at=starts_at,
            ends_at=ends_at,
            rrule=rrule,
            target_ids=target_ids,
            created_by=created_by,
        )
        db.add(mw)
        await db.flush()
        return mw

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, mw_id: uuid.UUID
    ) -> MaintenanceWindow | None:
        return (
            await db.execute(
                select(MaintenanceWindow)
                .where(MaintenanceWindow.org_id == org_id)
                .where(MaintenanceWindow.org_id == org_id)
                .where(MaintenanceWindow.org_id == org_id)
                .where(
                    MaintenanceWindow.id == mw_id, MaintenanceWindow.org_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[MaintenanceWindow]:
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .order_by(MaintenanceWindow.starts_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def list_active_at(
        db: AsyncSession, org_id: uuid.UUID, dt: datetime
    ) -> Sequence[MaintenanceWindow]:
        """Return all maintenance windows active exactly at `dt`.
        For v1, we simply check starts_at <= dt <= ends_at. RRULE is omitted from this quick check for now.
        """
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.starts_at <= dt, MaintenanceWindow.ends_at >= dt)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        mw_id: uuid.UUID,
        *,
        name: str | None = None,
        reason: str | None = None,
        reason_provided: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        rrule: str | None = None,
        rrule_provided: bool = False,
        target_ids: list[str] | None = None,
    ) -> MaintenanceWindow | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if reason_provided:
            values["reason"] = reason
        if starts_at is not None:
            values["starts_at"] = starts_at
        if ends_at is not None:
            values["ends_at"] = ends_at
        if rrule_provided:
            values["rrule"] = rrule
        if target_ids is not None:
            values["target_ids"] = target_ids
        if not values:
            return await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)
        stmt = (
            update(MaintenanceWindow)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.id == mw_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, mw_id: uuid.UUID) -> bool:
        mw = await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)
        if mw is None:
            return False
        await db.delete(mw)
        await db.flush()
        return True


class BotConnectorRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        platform: str,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
        allowed_capabilities: list[str],
        status: str = "not_configured",
        is_enabled: bool = False,
    ) -> BotConnector:
        connector = BotConnector(
            org_id=org_id,
            name=name,
            platform=platform,
            config=config,
            credentials=credentials,
            allowed_capabilities=allowed_capabilities,
            status=status,
            is_enabled=is_enabled,
        )
        db.add(connector)
        await db.flush()
        return connector

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, connector_id: uuid.UUID
    ) -> BotConnector | None:
        return (
            await db.execute(
                select(BotConnector)
                .where(BotConnector.org_id == org_id)
                .where(BotConnector.org_id == org_id)
                .where(BotConnector.org_id == org_id)
                .where(BotConnector.id == connector_id, BotConnector.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_name(
        db: AsyncSession, org_id: uuid.UUID, name: str
    ) -> BotConnector | None:
        stmt = (
            select(BotConnector)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.name == name)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        enabled_only: bool = False,
        platform: str | None = None,
    ) -> Sequence[BotConnector]:
        stmt = (
            select(BotConnector)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .order_by(BotConnector.name)
        )
        if enabled_only:
            stmt = stmt.where(BotConnector.is_enabled == True)
        if platform is not None:
            stmt = stmt.where(BotConnector.platform == platform)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        connector_id: uuid.UUID,
        *,
        name: str,
        platform: str,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
        allowed_capabilities: list[str],
        status: str,
        is_enabled: bool,
    ) -> BotConnector | None:
        stmt = (
            update(BotConnector)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.id == connector_id)
            .values(
                name=name,
                platform=platform,
                config=config,
                credentials=credentials,
                allowed_capabilities=allowed_capabilities,
                status=status,
                is_enabled=is_enabled,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await BotConnectorRepo.get_by_id(db, org_id, connector_id)

    @staticmethod
    async def mark_status(
        db: AsyncSession,
        org_id: uuid.UUID,
        connector_id: uuid.UUID,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        stmt = (
            update(BotConnector)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.org_id == org_id)
            .where(BotConnector.id == connector_id)
            .values(
                status=status,
                last_checked_at=datetime.now(timezone.utc),
                last_error=error,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(stmt)

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, connector_id: uuid.UUID
    ) -> bool:
        connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
        if connector is None:
            return False
        await db.delete(connector)
        await db.flush()
        return True


class BotUserLinkRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        platform_user_id: str,
        aim_user_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> BotUserLink:
        link = BotUserLink(
            org_id=org_id,
            connector_id=connector_id,
            platform_user_id=platform_user_id,
            aim_user_id=aim_user_id,
            created_by=created_by,
        )
        db.add(link)
        await db.flush()
        return link

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, link_id: uuid.UUID
    ) -> BotUserLink | None:
        return (
            await db.execute(
                select(BotUserLink)
                .where(BotUserLink.org_id == org_id)
                .where(BotUserLink.org_id == org_id)
                .where(BotUserLink.org_id == org_id)
                .where(BotUserLink.id == link_id, BotUserLink.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_platform_user(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        platform_user_id: str,
    ) -> BotUserLink | None:
        stmt = (
            select(BotUserLink)
            .where(BotUserLink.org_id == org_id)
            .where(BotUserLink.org_id == org_id)
            .where(BotUserLink.org_id == org_id)
            .where(
                BotUserLink.connector_id == connector_id,
                BotUserLink.platform_user_id == platform_user_id,
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_connector(
        db: AsyncSession, org_id: uuid.UUID, connector_id: uuid.UUID
    ) -> Sequence[BotUserLink]:
        stmt = (
            select(BotUserLink)
            .where(BotUserLink.org_id == org_id)
            .where(BotUserLink.org_id == org_id)
            .where(BotUserLink.org_id == org_id)
            .where(BotUserLink.connector_id == connector_id)
            .order_by(BotUserLink.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, link_id: uuid.UUID) -> bool:
        link = await BotUserLinkRepo.get_by_id(db, org_id, link_id)
        if link is None:
            return False
        await db.delete(link)
        await db.flush()
        return True


class BotActionAuditRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        platform: str,
        chat_id: str | None,
        command: str | None,
        status: str,
        detail: str | None = None,
        session_id: uuid.UUID | None = None,
    ) -> BotActionAudit:
        entry = BotActionAudit(
            org_id=org_id,
            connector_id=connector_id,
            platform=platform,
            chat_id=chat_id,
            command=command,
            status=status,
            detail=detail,
            session_id=session_id,
        )
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def list_by_connector(
        db: AsyncSession,
        org_id: uuid.UUID,
        connector_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[BotActionAudit]:
        stmt = (
            select(BotActionAudit)
            .where(BotActionAudit.org_id == org_id)
            .where(BotActionAudit.org_id == org_id)
            .where(BotActionAudit.org_id == org_id)
            .where(BotActionAudit.connector_id == connector_id)
            .order_by(BotActionAudit.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

class OrganizationRepo:
    @staticmethod
    async def create(db: AsyncSession, *, name: str, slug: str | None = None) -> Organization:
        org = Organization(name=name, slug=slug or name.lower().replace(" ", "-"))
        db.add(org)
        await db.flush()
        return org

    @staticmethod
    async def get_by_id(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
        return await db.get(Organization, org_id)

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[Organization]:
        result = await db.execute(select(Organization).order_by(Organization.name))
        return result.scalars().all()
