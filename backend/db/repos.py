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
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.models import (
    AgentTeamProfile,
    ApprovalRequest,
    AuditEntry,
    AuditFinding,
    AuditRun,
    AuditSchedule,
    BotActionAudit,
    BotConnector,
    BotUserLink,
    EscalationChain,
    EscalationStep,
    Incident,
    IncidentAssignment,
    IncidentChainState,
    IncidentComment,
    InAppNotification,
    NotificationEscalation,
    IncidentNotificationReceipt,
    IncidentMemory,
    IncidentMemoryRecallLog,
    IncidentPage,
    ServiceEscalationChain,
    PriorityLLMOverrideLog,
    PriorityRule,
    RetentionConfig,
    Roster,
    RosterMember,
    RosterOverride,
    Service,
    ServiceRoster,
    Team,
    TeamMember,
    IngestLog,
    IngestToken,
    MCPServer,
    MCPServerOAuthToken,
    ModelConfig,
    NativeActionInvocation,
    RuntimeConfig,
    Session,
    SessionMessage,
    Skill,
    User,
    UserNotificationPref,
    WebhookTrigger,
    WorkflowProfile,
    SLATarget,
    UptimeSample,
    UptimeSample1h,
    UptimeSample5m,
    SLO,
    MaintenanceWindow,
    Organization,
    OrganizationDomain,
    OrgInvite,
    OrgSAMLConfig,
    OrgSSOConfig,
    PasswordResetToken,
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
        auth_source: str = "local",
        role: str = "viewer",
        primary_org_id: uuid.UUID | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> User:
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            auth_source=auth_source,
            role=role,
            primary_org_id=primary_org_id,
            first_name=first_name,
            last_name=last_name,
        )
        db.add(user)
        await db.flush()
        return user

    @staticmethod
    async def set_primary_org(
        db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> None:
        """Update a user's primary organization context."""
        stmt = update(User).where(User.id == user_id).values(primary_org_id=org_id)
        await db.execute(stmt)
        await db.flush()

    @staticmethod
    async def add_to_organization(
        db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, role: str = "viewer"
    ) -> None:
        """Link a user to an organization."""
        link = UserOrganization(user_id=user_id, org_id=org_id, role=role)
        db.add(link)
        await db.flush()

    @staticmethod
    async def list_by_org(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[dict[str, Any]]:
        """List all users in an organization with their local roles."""
        from sqlalchemy import join

        stmt = (
            select(
                User.id.label("user_id"),
                User.username,
                User.email,
                UserOrganization.role,
                UserOrganization.joined_at,
            )
            .select_from(
                join(User, UserOrganization, User.id == UserOrganization.user_id)
            )
            .where(UserOrganization.org_id == org_id)
            .order_by(UserOrganization.joined_at)
        )
        result = await db.execute(stmt)
        return result.mappings().all()

    @staticmethod
    async def list_organizations(
        db: AsyncSession, user_id: uuid.UUID
    ) -> Sequence[dict[str, Any]]:
        """List all organizations a user belongs to with their per-org role."""
        from sqlalchemy import join

        stmt = (
            select(
                Organization.id,
                Organization.name,
                Organization.slug,
                Organization.branding,
                UserOrganization.role,
                UserOrganization.joined_at,
            )
            .select_from(
                join(
                    UserOrganization,
                    Organization,
                    UserOrganization.org_id == Organization.id,
                )
            )
            .where(UserOrganization.user_id == user_id)
            .order_by(UserOrganization.joined_at)
        )
        result = await db.execute(stmt)
        return result.mappings().all()

    @staticmethod
    async def is_member(
        db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> bool:
        stmt = select(UserOrganization).where(
            UserOrganization.user_id == user_id,
            UserOrganization.org_id == org_id,
        )
        result = await db.execute(stmt)
        return result.first() is not None

    @staticmethod
    async def remove_from_organization(
        db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> bool:
        """Remove a user's link to an organization."""
        from sqlalchemy import delete

        stmt = delete(UserOrganization).where(
            UserOrganization.user_id == user_id, UserOrganization.org_id == org_id
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0

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
        db: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> Sequence[User]:
        stmt = select(User).order_by(User.created_at).limit(limit).offset(offset)
        if not include_deleted:
            stmt = stmt.where(User.deleted_at.is_(None))
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update_fields(
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        auth_source: str | None = None,
        username: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar_color: str | None = None,
    ) -> User | None:
        """Admin or self patch — change role/active/profile fields. Only
        non-None arguments are applied."""

        values: dict[str, Any] = {}
        if role is not None:
            values["role"] = role
        if is_active is not None:
            values["is_active"] = is_active
        if auth_source is not None:
            values["auth_source"] = auth_source
        if username is not None:
            values["username"] = username
        if email is not None:
            values["email"] = email
        if first_name is not None:
            values["first_name"] = first_name
        if last_name is not None:
            values["last_name"] = last_name
        if avatar_color is not None:
            values["avatar_color"] = avatar_color
        if not values:
            return await db.get(User, user_id)
        stmt = update(User).where(User.id == user_id).values(**values)
        await db.execute(stmt)
        await db.flush()
        return await db.get(User, user_id)

    @staticmethod
    async def count_roster_memberships(
        db: AsyncSession, user_id: uuid.UUID
    ) -> int:
        """Sprint 56: gate for soft-delete. The user must be off every
        roster before deletion is allowed."""

        stmt = select(func.count(RosterMember.id)).where(
            RosterMember.user_id == user_id
        )
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    @staticmethod
    async def soft_delete(
        db: AsyncSession, user_id: uuid.UUID
    ) -> User | None:
        """Set ``deleted_at`` and scrub sensitive fields.

        Per owner direction (Session 135), the row is kept so past
        incidents continue to render the historical username. Email is
        replaced with a sentinel value to free the unique constraint
        for a future re-invite of the same address. Password hash is
        replaced with an empty string so the user cannot authenticate.
        """

        now = datetime.now(timezone.utc)
        scrubbed_email = f"deleted-{user_id}@deleted.opsmender.local"
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                deleted_at=now,
                is_active=False,
                email=scrubbed_email,
                password_hash="",
            )
        )
        await db.execute(stmt)
        await db.flush()
        return await db.get(User, user_id)


class PasswordResetTokenRepo:
    """Sprint 56: admin-minted one-time password reset tokens."""

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        issued_by_user_id: uuid.UUID | None,
    ) -> PasswordResetToken:
        row = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            issued_by_user_id=issued_by_user_id,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get_by_hash(
        db: AsyncSession, token_hash: str
    ) -> PasswordResetToken | None:
        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_used(
        db: AsyncSession, token_id: uuid.UUID
    ) -> None:
        stmt = (
            update(PasswordResetToken)
            .where(PasswordResetToken.id == token_id)
            .values(used_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)
        await db.flush()


class OrgInviteRepo:
    """Sprint 56: admin-minted invites to join an organization."""

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        email: str,
        role: str,
        token_hash: str,
        expires_at: datetime,
        invited_by_user_id: uuid.UUID | None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> OrgInvite:
        row = OrgInvite(
            org_id=org_id,
            email=email.lower().strip(),
            role=role,
            first_name=first_name,
            last_name=last_name,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=invited_by_user_id,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get_by_hash(
        db: AsyncSession, token_hash: str
    ) -> OrgInvite | None:
        stmt = select(OrgInvite).where(OrgInvite.token_hash == token_hash)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(
        db: AsyncSession, invite_id: uuid.UUID
    ) -> OrgInvite | None:
        return await db.get(OrgInvite, invite_id)

    @staticmethod
    async def list_for_org(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[OrgInvite]:
        stmt = (
            select(OrgInvite)
            .where(OrgInvite.org_id == org_id)
            .order_by(OrgInvite.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def mark_accepted(
        db: AsyncSession,
        invite_id: uuid.UUID,
        *,
        accepted_by_user_id: uuid.UUID,
    ) -> None:
        stmt = (
            update(OrgInvite)
            .where(OrgInvite.id == invite_id)
            .values(
                accepted_at=datetime.now(timezone.utc),
                accepted_by_user_id=accepted_by_user_id,
            )
        )
        await db.execute(stmt)
        await db.flush()

    @staticmethod
    async def mark_revoked(
        db: AsyncSession, invite_id: uuid.UUID
    ) -> None:
        stmt = (
            update(OrgInvite)
            .where(OrgInvite.id == invite_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)
        await db.flush()


class IncidentRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        title: str,
        description: str,
        severity: str | None = None,
        priority: str | None = None,
        response_mode: str | None = None,
        service_id: uuid.UUID | None = None,
        ingestion_model_config_id: uuid.UUID | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> Incident:
        incident = Incident(
            org_id=org_id,
            title=title,
            description=description,
            severity=severity,
            priority=priority,
            response_mode=response_mode,
            service_id=service_id,
            ingestion_model_config_id=ingestion_model_config_id,
            external_id=external_id,
            external_source=external_source,
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
        status: "str | Sequence[str] | None" = None,
        severity: "str | Sequence[str] | None" = None,
        service_id: uuid.UUID | None = None,
        team_id: "uuid.UUID | Sequence[uuid.UUID] | None" = None,
        source: "str | Sequence[str] | None" = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        query: str | None = None,
        exclude_statuses: "Sequence[str] | None" = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Incident]:
        stmt = IncidentRepo._filtered_select(
            org_id,
            status=status,
            severity=severity,
            service_id=service_id,
            team_id=team_id,
            source=source,
            updated_from=updated_from,
            updated_to=updated_to,
            query=query,
            exclude_statuses=exclude_statuses,
        ).order_by(Incident.updated_at.desc(), Incident.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    def _filtered_select(
        org_id: uuid.UUID,
        *,
        status: "str | Sequence[str] | None" = None,
        severity: "str | Sequence[str] | None" = None,
        service_id: uuid.UUID | None = None,
        team_id: "uuid.UUID | Sequence[uuid.UUID] | None" = None,
        source: "str | Sequence[str] | None" = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        query: str | None = None,
        exclude_statuses: "Sequence[str] | None" = None,
    ):
        # Each categorical filter accepts a scalar (backward compatible) or a
        # list — a list is an OR match (IN). An empty list means "no filter".
        def _as_list(value):
            if value is None:
                return []
            if isinstance(value, (list, tuple, set)):
                return [v for v in value if v is not None]
            return [value]

        statuses = _as_list(status)
        severities = _as_list(severity)
        team_ids = _as_list(team_id)
        sources = {str(s) for s in _as_list(source)}

        stmt = select(Incident).where(Incident.org_id == org_id)
        # Exclude statuses (e.g. "merged") unless explicitly requested via the
        # status filter — merged incidents are folded away by default.
        excluded = [s for s in (exclude_statuses or []) if s not in statuses]
        if excluded:
            stmt = stmt.where(Incident.status.not_in(excluded))
        if team_ids:
            stmt = stmt.join(Service, Service.id == Incident.service_id).where(
                Service.org_id == org_id,
                Service.team_id.in_(team_ids),
            )
        if statuses:
            stmt = stmt.where(Incident.status.in_(statuses))
        if severities:
            stmt = stmt.where(Incident.severity.in_(severities))
        if service_id is not None:
            stmt = stmt.where(Incident.service_id == service_id)
        # source: manual = no external source; ingested = has external source.
        # Selecting both (or neither) applies no source filter.
        if "manual" in sources and "ingested" not in sources:
            stmt = stmt.where(Incident.external_source.is_(None))
        elif "ingested" in sources and "manual" not in sources:
            stmt = stmt.where(Incident.external_source.is_not(None))
        if updated_from is not None:
            stmt = stmt.where(Incident.updated_at >= updated_from)
        if updated_to is not None:
            stmt = stmt.where(Incident.updated_at <= updated_to)
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                or_(
                    Incident.title.ilike(pattern),
                    Incident.description.ilike(pattern),
                    Incident.external_source.ilike(pattern),
                    Incident.external_id.ilike(pattern),
                )
            )
        return stmt

    @staticmethod
    async def count_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        status: "str | Sequence[str] | None" = None,
        severity: "str | Sequence[str] | None" = None,
        service_id: uuid.UUID | None = None,
        team_id: "uuid.UUID | Sequence[uuid.UUID] | None" = None,
        source: "str | Sequence[str] | None" = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        query: str | None = None,
        exclude_statuses: "Sequence[str] | None" = None,
    ) -> int:
        filtered = IncidentRepo._filtered_select(
            org_id,
            status=status,
            severity=severity,
            service_id=service_id,
            team_id=team_id,
            source=source,
            updated_from=updated_from,
            updated_to=updated_to,
            query=query,
            exclude_statuses=exclude_statuses,
        ).subquery()
        stmt = select(func.count()).select_from(filtered)
        result = await db.execute(stmt)
        return int(result.scalar_one())

    @staticmethod
    async def update_fields(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_id: uuid.UUID,
        *,
        status: str | None = None,
        severity: str | None = None,
        service_id: uuid.UUID | None = None,
        service_id_set: bool = False,
    ) -> Incident | None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if severity is not None:
            values["severity"] = severity
        if service_id_set:
            values["service_id"] = service_id
        if not values:
            return await IncidentRepo.get_by_id(db, org_id, incident_id)
        values["updated_at"] = datetime.now(timezone.utc)
        stmt = (
            update(Incident)
            .where(Incident.org_id == org_id, Incident.id == incident_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await IncidentRepo.get_by_id(db, org_id, incident_id)

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
        # Resolving/closing an incident stops any staged notification
        # escalation still in flight (single chokepoint for every resolve path).
        if status in ("resolved", "closed"):
            from backend.paging import notification_escalation as _ne

            await _ne.stop_escalation(
                db, org_id, incident_id=incident_id, status="resolved"
            )

    @staticmethod
    async def combine_into(
        db: AsyncSession,
        org_id: uuid.UUID,
        secondary_id: uuid.UUID,
        *,
        primary_id: uuid.UUID,
    ) -> None:
        """Fold *secondary_id* into *primary_id*: status→``merged`` + pointer.

        The row is kept (never deleted) so the audit trail and external
        fingerprint survive. Also stops any staged notification escalation, the
        same as resolve/close.
        """
        stmt = (
            update(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.id == secondary_id)
            .values(
                status="merged",
                merged_into_incident_id=primary_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(stmt)
        from backend.paging import notification_escalation as _ne

        await _ne.stop_escalation(
            db, org_id, incident_id=secondary_id, status="resolved"
        )

    @staticmethod
    async def list_merged_into(
        db: AsyncSession, org_id: uuid.UUID, primary_id: uuid.UUID
    ) -> Sequence[Incident]:
        """Secondaries folded into *primary_id* (for the combined-from panel)."""
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.merged_into_incident_id == primary_id)
            .order_by(Incident.created_at)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def set_postmortem(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_id: uuid.UUID,
        postmortem_md: str | None,
    ) -> None:
        """Set or clear the postmortem markdown for an incident.

        Sprint 61 Step 4. Passing ``None`` (or an empty/whitespace string)
        clears the postmortem and its timestamp.
        """
        cleaned = postmortem_md.strip() if postmortem_md is not None else None
        now = datetime.now(timezone.utc) if cleaned else None
        stmt = (
            update(Incident)
            .where(Incident.org_id == org_id)
            .where(Incident.id == incident_id)
            .values(
                postmortem_md=cleaned or None,
                postmortem_updated_at=now,
            )
        )
        await db.execute(stmt)

    @staticmethod
    async def get_by_external_fingerprint(
        db: AsyncSession, org_id: uuid.UUID, *, external_source: str, external_id: str
    ) -> Incident | None:
        """Look up an incident by its external fingerprint for dedup.

        When the matched incident was folded into a primary via ``combine``
        (status ``merged``, ``merged_into_incident_id`` set), the merge chain
        is followed so a re-firing alert acts on the surviving primary instead
        of the merged-away secondary. Cyclic/broken pointers fall back to the
        last reachable incident.
        """
        stmt = (
            select(Incident)
            .where(Incident.org_id == org_id)
            .where(
                Incident.external_source == external_source,
                Incident.external_id == external_id,
            )
            .order_by(Incident.created_at.desc())
            .limit(1)
        )
        incident = (await db.execute(stmt)).scalar_one_or_none()
        if incident is None:
            return None
        seen: set[uuid.UUID] = set()
        while (
            incident.merged_into_incident_id is not None
            and incident.id not in seen
        ):
            seen.add(incident.id)
            primary = await IncidentRepo.get_by_id(
                db, org_id, incident.merged_into_incident_id
            )
            if primary is None:
                break
            incident = primary
        return incident

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

    @staticmethod
    async def delete_permanently(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_id: uuid.UUID,
    ) -> bool:
        """Delete an incident and its operational history in one transaction.

        Independent audit/source records are retained with incident/session
        references cleared. The caller owns commit/rollback.
        """
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            return False

        session_ids = list(
            (
                await db.execute(
                    select(Session.id).where(
                        Session.org_id == org_id,
                        Session.incident_id == incident_id,
                    )
                )
            ).scalars()
        )

        await db.execute(
            update(IngestLog)
            .where(IngestLog.org_id == org_id, IngestLog.incident_id == incident_id)
            .values(incident_id=None)
        )
        await db.execute(
            update(BotActionAudit)
            .where(
                BotActionAudit.org_id == org_id,
                BotActionAudit.incident_id == incident_id,
            )
            .values(incident_id=None)
        )
        await db.execute(
            update(IncidentMemory)
            .where(
                IncidentMemory.org_id == org_id,
                IncidentMemory.source_incident_id == incident_id,
            )
            .values(source_incident_id=None)
        )

        if session_ids:
            await db.execute(
                update(BotActionAudit)
                .where(
                    BotActionAudit.org_id == org_id,
                    BotActionAudit.session_id.in_(session_ids),
                )
                .values(session_id=None)
            )
            await db.execute(
                update(AuditFinding)
                .where(
                    AuditFinding.org_id == org_id,
                    AuditFinding.session_id.in_(session_ids),
                )
                .values(session_id=None)
            )
            await db.execute(
                delete(IncidentMemoryRecallLog).where(
                    IncidentMemoryRecallLog.session_id.in_(session_ids)
                )
            )
            await db.execute(
                delete(SessionMessage).where(
                    SessionMessage.org_id == org_id,
                    SessionMessage.session_id.in_(session_ids),
                )
            )
            await db.execute(
                delete(ApprovalRequest).where(
                    ApprovalRequest.org_id == org_id,
                    ApprovalRequest.session_id.in_(session_ids),
                )
            )
            await db.execute(
                delete(AuditEntry).where(
                    AuditEntry.org_id == org_id,
                    AuditEntry.session_id.in_(session_ids),
                )
            )

        for model in (
            IncidentNotificationReceipt,
            NativeActionInvocation,
            PriorityLLMOverrideLog,
            IncidentComment,
            IncidentAssignment,
            IncidentPage,
            IncidentChainState,
            NotificationEscalation,
        ):
            await db.execute(
                delete(model).where(
                    model.org_id == org_id,
                    model.incident_id == incident_id,
                )
            )

        if session_ids:
            await db.execute(
                delete(Session).where(
                    Session.org_id == org_id,
                    Session.id.in_(session_ids),
                )
            )
        await db.execute(
            delete(Incident).where(
                Incident.org_id == org_id,
                Incident.id == incident_id,
            )
        )
        await db.flush()
        return True


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
    async def list_for_incidents(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_ids: Sequence[uuid.UUID],
    ) -> dict[uuid.UUID, list[Session]]:
        """Sessions grouped by incident, newest-first within each incident.

        Used to surface AI-session state on the incidents list without an N+1
        per-incident lookup.
        """
        if not incident_ids:
            return {}
        stmt = (
            select(Session)
            .where(
                Session.org_id == org_id,
                Session.incident_id.in_(incident_ids),
            )
            .order_by(Session.started_at.desc())
        )
        grouped: dict[uuid.UUID, list[Session]] = {}
        for row in (await db.execute(stmt)).scalars().all():
            grouped.setdefault(row.incident_id, []).append(row)
        return grouped

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
        resolution_note: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": status,
            "resolved_at": datetime.now(timezone.utc),
            "resolved_by": resolved_by,
        }
        if resolution_note is not None:
            values["resolution_note"] = resolution_note
        stmt = (
            update(ApprovalRequest)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(ApprovalRequest.org_id == org_id)
            .where(
                ApprovalRequest.id == request_id, ApprovalRequest.status == "pending"
            )
            .values(**values)
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
        provider_meta: dict[str, str] | None = None,
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
            provider_meta=provider_meta,
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
        provider_meta: dict[str, str] | None = None,
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
                provider_meta=provider_meta,
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
        provider_meta: dict[str, str] | None = None,
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
                provider_meta=provider_meta,
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
                provider_meta=provider_meta,
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

    @staticmethod
    async def mark_connection_success(
        db: AsyncSession,
        org_id: uuid.UUID,
        server_id: uuid.UUID,
        *,
        at: datetime | None = None,
    ) -> MCPServer | None:
        stmt = (
            update(MCPServer)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.id == server_id)
            .values(
                last_successful_call_at=at or datetime.now(timezone.utc),
                last_error=None,
            )
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await MCPServerRepo.get_by_id(db, org_id, server_id)

    @staticmethod
    async def mark_connection_failure(
        db: AsyncSession,
        org_id: uuid.UUID,
        server_id: uuid.UUID,
        *,
        error: str,
    ) -> MCPServer | None:
        stmt = (
            update(MCPServer)
            .where(MCPServer.org_id == org_id)
            .where(MCPServer.id == server_id)
            .values(last_error=error)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await MCPServerRepo.get_by_id(db, org_id, server_id)


class MCPServerOAuthTokenRepo:
    """OAuth 2.1 token persistence for HTTP-transport MCP servers (Sprint 42).

    Tokens are encrypted at rest using the project's Fernet helper in
    ``backend/auth/secrets.py`` — callers pass plaintext and read
    plaintext; the repository owns the encrypt/decrypt boundary.

    Refresh-token rotation (OAuth 2.1 §4.3.1) is the common case: every
    successful token-endpoint response carries a *new* refresh_token
    that supersedes the old one. ``rotate`` writes both the new access
    token and the new refresh token in one shot and bumps
    ``last_refreshed_at``.
    """

    @staticmethod
    async def upsert(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        mcp_server_id: uuid.UUID,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scopes: list[str] | None = None,
        issuer: str | None = None,
        token_type: str = "Bearer",
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> MCPServerOAuthToken:
        """Create the token row, or replace the existing one for this server.

        Used after the initial OAuth code exchange and any later forced
        re-authorization. For routine refresh-token rotation use
        :meth:`rotate` instead.

        ``client_id`` and ``client_secret`` are the OAuth client credentials
        used for the token endpoint. Stored so the refresh path can
        reconstruct ``ClientRegistration`` without re-running DCR.
        """

        from backend.auth.secrets import encrypt_secret

        existing = await MCPServerOAuthTokenRepo.get_for_server(
            db, org_id, mcp_server_id
        )
        if existing is not None:
            existing.access_token_encrypted = encrypt_secret(access_token)
            existing.refresh_token_encrypted = (
                encrypt_secret(refresh_token) if refresh_token else None
            )
            existing.expires_at = expires_at
            existing.scopes = scopes
            existing.issuer = issuer
            existing.token_type = token_type
            existing.client_id = client_id
            existing.client_secret_encrypted = (
                encrypt_secret(client_secret) if client_secret else None
            )
            existing.obtained_at = datetime.now(timezone.utc)
            existing.last_refreshed_at = None
            await db.flush()
            return existing

        row = MCPServerOAuthToken(
            org_id=org_id,
            mcp_server_id=mcp_server_id,
            access_token_encrypted=encrypt_secret(access_token),
            refresh_token_encrypted=(
                encrypt_secret(refresh_token) if refresh_token else None
            ),
            token_type=token_type,
            scopes=scopes,
            expires_at=expires_at,
            issuer=issuer,
            client_id=client_id,
            client_secret_encrypted=(
                encrypt_secret(client_secret) if client_secret else None
            ),
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def rotate(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        mcp_server_id: uuid.UUID,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scopes: list[str] | None = None,
    ) -> MCPServerOAuthToken | None:
        """Update an existing token row after a successful refresh.

        Per OAuth 2.1 §4.3.1, public-client refresh responses rotate the
        refresh_token — callers MUST pass the new value (or ``None`` if
        the authorization server didn't issue one this round).
        """

        from backend.auth.secrets import encrypt_secret

        row = await MCPServerOAuthTokenRepo.get_for_server(db, org_id, mcp_server_id)
        if row is None:
            return None
        row.access_token_encrypted = encrypt_secret(access_token)
        if refresh_token is not None:
            row.refresh_token_encrypted = encrypt_secret(refresh_token)
        # If the response omitted refresh_token we keep the existing one
        # (the AS opted not to rotate this turn — still valid per spec).
        row.expires_at = expires_at
        if scopes is not None:
            row.scopes = scopes
        row.last_refreshed_at = datetime.now(timezone.utc)
        await db.flush()
        return row

    @staticmethod
    async def get_for_server(
        db: AsyncSession,
        org_id: uuid.UUID,
        mcp_server_id: uuid.UUID,
    ) -> MCPServerOAuthToken | None:
        return (
            await db.execute(
                select(MCPServerOAuthToken).where(
                    MCPServerOAuthToken.org_id == org_id,
                    MCPServerOAuthToken.mcp_server_id == mcp_server_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def read_plaintext(
        row: MCPServerOAuthToken,
    ) -> tuple[str, str | None]:
        """Decrypt and return ``(access_token, refresh_token)``.

        Returned as a plain tuple so callers can hand the values
        directly to ``httpx.AsyncClient`` without juggling the model
        instance.
        """

        from backend.auth.secrets import decrypt_secret

        access = decrypt_secret(row.access_token_encrypted)
        refresh = (
            decrypt_secret(row.refresh_token_encrypted)
            if row.refresh_token_encrypted
            else None
        )
        return access, refresh

    @staticmethod
    async def read_client_credentials(
        row: MCPServerOAuthToken,
    ) -> tuple[str | None, str | None]:
        """Decrypt and return ``(client_id, client_secret)``.

        ``client_id`` is stored plaintext; ``client_secret`` is Fernet-
        encrypted. Either may be ``None`` for public clients or when
        credentials were not captured during authorization.
        """

        from backend.auth.secrets import decrypt_secret

        client_id = row.client_id
        client_secret = (
            decrypt_secret(row.client_secret_encrypted)
            if row.client_secret_encrypted
            else None
        )
        return client_id, client_secret

    @staticmethod
    async def map_by_server_id(
        db: AsyncSession,
        org_id: uuid.UUID,
    ) -> dict[uuid.UUID, "MCPServerOAuthToken"]:
        """Return a ``{mcp_server_id: token_row}`` dict for all org tokens.

        Used by the list endpoint to annotate each server with its OAuth
        status in a single query rather than N per-server lookups.
        """

        result = await db.execute(
            select(MCPServerOAuthToken).where(
                MCPServerOAuthToken.org_id == org_id
            )
        )
        return {row.mcp_server_id: row for row in result.scalars().all()}

    @staticmethod
    async def list_expiring_before(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        cutoff: datetime,
    ) -> Sequence[MCPServerOAuthToken]:
        """Tokens with ``expires_at <= cutoff`` — the auto-refresh sweep query."""

        stmt = (
            select(MCPServerOAuthToken)
            .where(MCPServerOAuthToken.org_id == org_id)
            .where(MCPServerOAuthToken.expires_at.is_not(None))
            .where(MCPServerOAuthToken.expires_at <= cutoff)
            .order_by(MCPServerOAuthToken.expires_at)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def delete_for_server(
        db: AsyncSession,
        org_id: uuid.UUID,
        mcp_server_id: uuid.UUID,
    ) -> bool:
        """Remove the OAuth row when the operator disconnects the server."""

        row = await MCPServerOAuthTokenRepo.get_for_server(db, org_id, mcp_server_id)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True


class SkillRepo:
    @staticmethod
    def _resolve_assignment(
        assignment: str | None, mcp_server_id: uuid.UUID | None
    ) -> tuple[str, uuid.UUID | None]:
        """Normalize (assignment, mcp_server_id) into a consistent pair.

        A server-bound skill keeps its mcp_server_id; global/unassigned skills
        never carry one. When assignment is omitted, infer it from
        mcp_server_id (set -> server, else global) for backward compatibility.
        """
        if assignment is None:
            assignment = "server" if mcp_server_id is not None else "global"
        if assignment == "server":
            return "server", mcp_server_id
        # global / unassigned never bind to a server.
        return assignment, None

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        content_md: str,
        description: str | None = None,
        mcp_server_id: uuid.UUID | None = None,
        assignment: str | None = None,
    ) -> Skill:
        assignment, mcp_server_id = SkillRepo._resolve_assignment(
            assignment, mcp_server_id
        )
        skill = Skill(
            org_id=org_id,
            name=name,
            description=description,
            mcp_server_id=mcp_server_id,
            assignment=assignment,
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
        """Return the effective skill for a given MCP server.

        Precedence: a server-specific skill (``mcp_server_id`` match) wins;
        otherwise the global-fallback skill (``assignment == 'global'``). A
        skill assigned ``unassigned`` is a draft and is **never** returned —
        it must not be injected into AI sessions. Returns ``None`` if neither
        a server-specific nor a global skill exists.
        """
        if mcp_server_id is not None:
            stmt = (
                select(Skill)
                .where(Skill.org_id == org_id)
                .where(Skill.mcp_server_id == mcp_server_id)
                .where(Skill.assignment != "unassigned")
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
            .where(Skill.mcp_server_id.is_(None))
            .where(Skill.assignment == "global")
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
        assignment: str | None = None,
    ) -> Skill | None:
        assignment, mcp_server_id = SkillRepo._resolve_assignment(
            assignment, mcp_server_id
        )
        stmt = (
            update(Skill)
            .where(Skill.org_id == org_id)
            .where(Skill.id == skill_id)
            .values(
                name=name,
                content_md=content_md,
                description=description,
                mcp_server_id=mcp_server_id,
                assignment=assignment,
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
    async def get_global_value(db: AsyncSession, key: str) -> str | None:
        """Return the most-recently-updated value for *key* across all orgs.

        Used for process-global settings (e.g. ``logging_level``) that are
        stored per-org but applied process-wide. In single-workspace mode
        there is exactly one row; with multiple orgs the newest write wins.
        """
        stmt = (
            select(RuntimeConfig)
            .where(RuntimeConfig.key == key)
            .order_by(RuntimeConfig.updated_at.desc())
            .limit(1)
        )
        item = (await db.execute(stmt)).scalars().first()
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
        service_id: uuid.UUID | None = None,
    ) -> IngestToken:
        token = IngestToken(
            org_id=org_id,
            name=name,
            provider=provider,
            token_hash=token_hash,
            shape_cache=shape_cache,
            service_id=service_id,
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
    async def get_active_for_service(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> IngestToken | None:
        stmt = (
            select(IngestToken)
            .where(IngestToken.org_id == org_id)
            .where(IngestToken.service_id == service_id)
            .where(IngestToken.is_active == True)
            .order_by(IngestToken.created_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().first()

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
    async def list_all_global(
        db: AsyncSession, *, active_only: bool = False
    ) -> Sequence[IngestToken]:
        stmt = select(IngestToken).order_by(IngestToken.created_at.desc())
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

    @staticmethod
    async def list_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[IngestLog]:
        stmt = (
            select(IngestLog)
            .where(IngestLog.org_id == org_id)
            .where(IngestLog.incident_id == incident_id)
            .order_by(IngestLog.created_at.desc())
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
        service_id: uuid.UUID | None = None,
        is_active: bool = True,
    ) -> SLATarget:
        target = SLATarget(
            org_id=org_id,
            name=name,
            kind=kind,
            config=config,
            owner_team=owner_team,
            service_id=service_id,
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
        service_id: uuid.UUID | None = None,
        service_id_provided: bool = False,
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
        if service_id_provided:
            values["service_id"] = service_id
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
    async def latest_sample(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> UptimeSample | None:
        """Most recent sample for a target (drives current status + last check)."""
        stmt = (
            select(UptimeSample)
            .where(UptimeSample.org_id == org_id)
            .where(UptimeSample.target_id == target_id)
            .order_by(UptimeSample.observed_at.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalars().first()

    @staticmethod
    async def query_5m_window(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
    ) -> Sequence[UptimeSample5m]:
        stmt = (
            select(UptimeSample5m)
            .where(
                UptimeSample5m.org_id == org_id,
                UptimeSample5m.target_id == target_id,
                UptimeSample5m.bucket_start >= since,
                UptimeSample5m.bucket_start <= until,
            )
            .order_by(UptimeSample5m.bucket_start)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def query_1h_window(
        db: AsyncSession,
        org_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
    ) -> Sequence[UptimeSample1h]:
        stmt = (
            select(UptimeSample1h)
            .where(
                UptimeSample1h.org_id == org_id,
                UptimeSample1h.target_id == target_id,
                UptimeSample1h.bucket_start >= since,
                UptimeSample1h.bucket_start <= until,
            )
            .order_by(UptimeSample1h.bucket_start)
        )
        return (await db.execute(stmt)).scalars().all()

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
        from backend.sla.metrics import uptime_stats

        until = until or datetime.now(timezone.utc)
        samples = await UptimeSampleRepo.query_window(
            db, org_id, target_id, since=since, until=until
        )
        return uptime_stats(samples)


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
        description: str | None = None,
        starts_at: datetime,
        ends_at: datetime,
        rrule: str | None = None,
        target_ids: list[str] | None = None,
        scope_type: str = "global",
        scope_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        approved: bool = True,
        approved_by: uuid.UUID | None = None,
        approved_at: datetime | None = None,
    ) -> MaintenanceWindow:
        mw = MaintenanceWindow(
            org_id=org_id,
            name=name,
            reason=reason,
            description=description,
            starts_at=starts_at,
            ends_at=ends_at,
            rrule=rrule,
            target_ids=target_ids or [],
            scope_type=scope_type,
            scope_id=scope_id,
            created_by=created_by,
            approved=approved,
            approved_by=approved_by,
            approved_at=approved_at,
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
        db: AsyncSession,
        org_id: uuid.UUID,
        dt: datetime,
        *,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> Sequence[MaintenanceWindow]:
        """Return maintenance windows active exactly at ``dt``.

        RRULE is omitted from this quick check for now. When a scope is
        provided, global windows are included alongside matching scoped ones.
        """
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.org_id == org_id)
            .where(MaintenanceWindow.starts_at <= dt, MaintenanceWindow.ends_at > dt)
            # Pending (unapproved) windows do not suppress alerts.
            .where(MaintenanceWindow.approved == True)  # noqa: E712
        )
        if scope_type is not None:
            from sqlalchemy import and_, or_

            scoped_match = and_(
                MaintenanceWindow.scope_type == scope_type,
                MaintenanceWindow.scope_id == scope_id,
            )
            stmt = stmt.where(
                or_(MaintenanceWindow.scope_type == "global", scoped_match)
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
        description: str | None = None,
        description_provided: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        rrule: str | None = None,
        rrule_provided: bool = False,
        target_ids: list[str] | None = None,
        scope_type: str | None = None,
        scope_id: uuid.UUID | None = None,
        scope_id_provided: bool = False,
    ) -> MaintenanceWindow | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if reason_provided:
            values["reason"] = reason
        if description_provided:
            values["description"] = description
        if starts_at is not None:
            values["starts_at"] = starts_at
        if ends_at is not None:
            values["ends_at"] = ends_at
        if rrule_provided:
            values["rrule"] = rrule
        if target_ids is not None:
            values["target_ids"] = target_ids
        if scope_type is not None:
            values["scope_type"] = scope_type
        if scope_id_provided:
            values["scope_id"] = scope_id
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


class UserNotificationPrefRepo:
    @staticmethod
    async def get_for_user(
        db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> UserNotificationPref | None:
        stmt = select(UserNotificationPref).where(
            UserNotificationPref.org_id == org_id,
            UserNotificationPref.user_id == user_id,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        channels: dict[str, Any] | None = None,
        routing: dict[str, Any] | None = None,
        quiet_hours: dict[str, Any] | None = None,
        quiet_hours_provided: bool = False,
    ) -> UserNotificationPref:
        pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user_id)
        if pref is None:
            pref = UserNotificationPref(
                org_id=org_id,
                user_id=user_id,
                channels=channels or {},
                routing=routing or {},
                quiet_hours=quiet_hours if quiet_hours_provided else None,
            )
            db.add(pref)
            await db.flush()
            return pref

        if channels is not None:
            pref.channels = channels
        if routing is not None:
            pref.routing = routing
        if quiet_hours_provided:
            pref.quiet_hours = quiet_hours
        pref.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return pref


class InAppNotificationRepo:
    """Per-user in-app notifications (the bell / notification center)."""

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        event_type: str,
        category: str,
        title: str,
        body: str | None = None,
        link: str | None = None,
        incident_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> InAppNotification:
        notification = InAppNotification(
            org_id=org_id,
            user_id=user_id,
            event_type=event_type,
            category=category,
            title=title,
            body=body,
            link=link,
            incident_id=incident_id,
            session_id=session_id,
        )
        db.add(notification)
        await db.flush()
        return notification

    @staticmethod
    async def list_for_user(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[InAppNotification]:
        stmt = select(InAppNotification).where(
            InAppNotification.org_id == org_id,
            InAppNotification.user_id == user_id,
        )
        if unread_only:
            stmt = stmt.where(InAppNotification.read_at.is_(None))
        stmt = (
            stmt.order_by(InAppNotification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def count_for_user(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        unread_only: bool = False,
    ) -> int:
        stmt = select(func.count(InAppNotification.id)).where(
            InAppNotification.org_id == org_id,
            InAppNotification.user_id == user_id,
        )
        if unread_only:
            stmt = stmt.where(InAppNotification.read_at.is_(None))
        return int((await db.execute(stmt)).scalar_one())

    @staticmethod
    async def get_by_id(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        notification_id: uuid.UUID,
    ) -> InAppNotification | None:
        stmt = select(InAppNotification).where(
            InAppNotification.org_id == org_id,
            InAppNotification.user_id == user_id,
            InAppNotification.id == notification_id,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def mark_read(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        notification_id: uuid.UUID,
        *,
        read: bool = True,
    ) -> bool:
        """Mark a single notification (un)read. Returns False if not found."""
        notification = await InAppNotificationRepo.get_by_id(
            db, org_id, user_id, notification_id
        )
        if notification is None:
            return False
        notification.read_at = datetime.now(timezone.utc) if read else None
        await db.flush()
        return True

    @staticmethod
    async def mark_all_read(
        db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> int:
        """Mark every unread notification for a user read. Returns the count."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(InAppNotification)
            .where(
                InAppNotification.org_id == org_id,
                InAppNotification.user_id == user_id,
                InAppNotification.read_at.is_(None),
            )
            .values(read_at=now)
        )
        result = await db.execute(stmt)
        return int(result.rowcount or 0)

    @staticmethod
    async def delete(
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        notification_id: uuid.UUID,
    ) -> bool:
        stmt = delete(InAppNotification).where(
            InAppNotification.org_id == org_id,
            InAppNotification.user_id == user_id,
            InAppNotification.id == notification_id,
        )
        result = await db.execute(stmt)
        return bool(result.rowcount or 0)


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
        native_actions_enabled: bool = False,
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
            native_actions_enabled=native_actions_enabled,
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
        native_actions_enabled: bool = False,
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
                native_actions_enabled=native_actions_enabled,
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
    async def mark_callback_verified(
        db: AsyncSession,
        org_id: uuid.UUID,
        connector_id: uuid.UUID,
    ) -> None:
        now = datetime.now(timezone.utc)
        await db.execute(
            update(BotConnector)
            .where(
                BotConnector.id == connector_id,
                BotConnector.org_id == org_id,
            )
            .values(
                callback_status="verified",
                callback_last_verified_at=now,
                callback_last_error=None,
                updated_at=now,
            )
        )

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
        opsmender_user_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
        external_username: str | None = None,
        external_display_name: str | None = None,
        verified: bool = True,
    ) -> BotUserLink:
        link = BotUserLink(
            org_id=org_id,
            connector_id=connector_id,
            platform_user_id=platform_user_id,
            opsmender_user_id=opsmender_user_id,
            created_by=created_by,
            external_username=external_username,
            external_display_name=external_display_name,
            verified=verified,
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
    async def mark_seen(
        db: AsyncSession,
        org_id: uuid.UUID,
        link_id: uuid.UUID,
        *,
        external_username: str | None = None,
        external_display_name: str | None = None,
    ) -> BotUserLink | None:
        values: dict[str, Any] = {"last_seen_at": datetime.now(timezone.utc)}
        if external_username is not None:
            values["external_username"] = external_username
        if external_display_name is not None:
            values["external_display_name"] = external_display_name
        result = await db.execute(
            update(BotUserLink)
            .where(BotUserLink.org_id == org_id, BotUserLink.id == link_id)
            .values(**values)
        )
        if not result.rowcount:
            return None
        await db.flush()
        return await BotUserLinkRepo.get_by_id(db, org_id, link_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, link_id: uuid.UUID) -> bool:
        link = await BotUserLinkRepo.get_by_id(db, org_id, link_id)
        if link is None:
            return False
        await db.delete(link)
        await db.flush()
        return True


class IncidentNotificationReceiptRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        connector_id: uuid.UUID,
        platform: str,
        lifecycle_event: str,
        external_channel_id: str | None = None,
        external_message_id: str | None = None,
        external_thread_id: str | None = None,
        rendered_status: str | None = None,
        can_update: bool = False,
        session_id: uuid.UUID | None = None,
        delivery_status: str = "delivered",
        update_failed_reason: str | None = None,
        last_updated_at: datetime | None = None,
    ) -> IncidentNotificationReceipt:
        row = IncidentNotificationReceipt(
            org_id=org_id,
            incident_id=incident_id,
            connector_id=connector_id,
            session_id=session_id,
            platform=platform,
            external_channel_id=external_channel_id,
            external_message_id=external_message_id,
            external_thread_id=external_thread_id,
            lifecycle_event=lifecycle_event,
            rendered_status=rendered_status,
            can_update=can_update,
            delivery_status=delivery_status,
            update_failed_reason=update_failed_reason,
            last_updated_at=last_updated_at,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def latest_for_incident_channel(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        connector_id: uuid.UUID,
        external_channel_id: str | None,
        updateable_only: bool = False,
    ) -> IncidentNotificationReceipt | None:
        stmt = (
            select(IncidentNotificationReceipt)
            .where(
                IncidentNotificationReceipt.org_id == org_id,
                IncidentNotificationReceipt.incident_id == incident_id,
                IncidentNotificationReceipt.connector_id == connector_id,
                IncidentNotificationReceipt.external_channel_id == external_channel_id,
            )
            .order_by(IncidentNotificationReceipt.created_at.desc())
            .limit(1)
        )
        if updateable_only:
            stmt = stmt.where(
                IncidentNotificationReceipt.can_update == True,
                IncidentNotificationReceipt.external_message_id.is_not(None),
            )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_incident(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_id: uuid.UUID,
    ) -> Sequence[IncidentNotificationReceipt]:
        stmt = (
            select(IncidentNotificationReceipt)
            .where(
                IncidentNotificationReceipt.org_id == org_id,
                IncidentNotificationReceipt.incident_id == incident_id,
            )
            .order_by(IncidentNotificationReceipt.created_at)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


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
        incident_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        external_user_id: str | None = None,
        idempotency_key: str | None = None,
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
            incident_id=incident_id,
            actor_user_id=actor_user_id,
            external_user_id=external_user_id,
            idempotency_key=idempotency_key,
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


class NativeActionInvocationRepo:
    @staticmethod
    async def get_by_key(
        db: AsyncSession,
        org_id: uuid.UUID,
        connector_id: uuid.UUID,
        idempotency_key: str,
    ) -> NativeActionInvocation | None:
        return (
            await db.execute(
                select(NativeActionInvocation).where(
                    NativeActionInvocation.org_id == org_id,
                    NativeActionInvocation.connector_id == connector_id,
                    NativeActionInvocation.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        platform: str,
        idempotency_key: str,
        incident_id: uuid.UUID,
        action: str,
        external_user_id: str,
        callback_received_at: datetime,
    ) -> NativeActionInvocation:
        row = NativeActionInvocation(
            org_id=org_id,
            connector_id=connector_id,
            platform=platform,
            idempotency_key=idempotency_key,
            incident_id=incident_id,
            action=action,
            external_user_id=external_user_id,
            callback_received_at=callback_received_at,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def reserve(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        platform: str,
        idempotency_key: str,
        incident_id: uuid.UUID,
        action: str,
        external_user_id: str,
        callback_received_at: datetime,
    ) -> tuple[NativeActionInvocation, bool]:
        existing = await NativeActionInvocationRepo.get_by_key(
            db, org_id, connector_id, idempotency_key
        )
        if existing is not None:
            return existing, False
        try:
            async with db.begin_nested():
                row = await NativeActionInvocationRepo.create(
                    db,
                    org_id,
                    connector_id=connector_id,
                    platform=platform,
                    idempotency_key=idempotency_key,
                    incident_id=incident_id,
                    action=action,
                    external_user_id=external_user_id,
                    callback_received_at=callback_received_at,
                )
            return row, True
        except IntegrityError:
            existing = await NativeActionInvocationRepo.get_by_key(
                db, org_id, connector_id, idempotency_key
            )
            if existing is None:
                raise
            return existing, False

    @staticmethod
    async def finish(
        db: AsyncSession,
        invocation: NativeActionInvocation,
        *,
        status: str,
        actor_user_id: uuid.UUID | None = None,
        result_status: str | None = None,
        error_code: str | None = None,
        session_id: uuid.UUID | None = None,
    ) -> NativeActionInvocation:
        invocation.status = status
        invocation.actor_user_id = actor_user_id
        invocation.result_status = result_status
        invocation.error_code = error_code
        invocation.session_id = session_id
        invocation.completed_at = datetime.now(timezone.utc)
        invocation.updated_at = invocation.completed_at
        await db.flush()
        return invocation


class OrganizationRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        name: str,
        slug: str | None = None,
        branding: dict | None = None,
    ) -> Organization:
        org = Organization(
            name=name, slug=slug or name.lower().replace(" ", "-"), branding=branding
        )
        db.add(org)
        await db.flush()
        return org

    @staticmethod
    async def get_by_id(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
        return await db.get(Organization, org_id)

    @staticmethod
    async def get_by_slug(db: AsyncSession, slug: str) -> Organization | None:
        stmt = select(Organization).where(Organization.slug == slug)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession) -> Sequence[Organization]:
        result = await db.execute(select(Organization).order_by(Organization.name))
        return result.scalars().all()

    @staticmethod
    async def count(db: AsyncSession) -> int:
        result = await db.execute(select(func.count()).select_from(Organization))
        return int(result.scalar_one())

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str | None = None,
        slug: str | None = None,
        branding: dict | None = None,
        notification_dedup_window_minutes: int | None = None,
        slack_incident_channels_enabled: bool | None = None,
    ) -> Organization | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if slug is not None:
            values["slug"] = slug
        if branding is not None:
            values["branding"] = branding
        if notification_dedup_window_minutes is not None:
            values["notification_dedup_window_minutes"] = (
                notification_dedup_window_minutes
            )
        if slack_incident_channels_enabled is not None:
            values["slack_incident_channels_enabled"] = (
                slack_incident_channels_enabled
            )

        if not values:
            return await OrganizationRepo.get_by_id(db, org_id)

        stmt = update(Organization).where(Organization.id == org_id).values(**values)
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await OrganizationRepo.get_by_id(db, org_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID) -> bool:
        org = await OrganizationRepo.get_by_id(db, org_id)
        if org is None:
            return False
        await db.delete(org)
        await db.flush()
        return True


class OrganizationDomainRepo:
    @staticmethod
    def normalize(domain: str) -> str:
        """Strip scheme/port and lowercase. Hostnames are case-insensitive."""
        d = domain.strip().lower()
        # Drop scheme if a full URL was pasted in.
        if "://" in d:
            d = d.split("://", 1)[1]
        # Drop path.
        d = d.split("/", 1)[0]
        # Drop port (Host header may include it; lookups must match the bare host).
        if ":" in d:
            d = d.split(":", 1)[0]
        return d

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        domain: str,
        is_primary: bool = False,
        verified: bool = True,
    ) -> OrganizationDomain:
        row = OrganizationDomain(
            org_id=org_id,
            domain=OrganizationDomainRepo.normalize(domain),
            is_primary=is_primary,
            verified=verified,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_for_org(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[OrganizationDomain]:
        stmt = (
            select(OrganizationDomain)
            .where(OrganizationDomain.org_id == org_id)
            .order_by(
                OrganizationDomain.is_primary.desc(), OrganizationDomain.created_at
            )
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_by_id(
        db: AsyncSession, domain_id: uuid.UUID
    ) -> OrganizationDomain | None:
        return await db.get(OrganizationDomain, domain_id)

    @staticmethod
    async def find_by_host(db: AsyncSession, host: str) -> OrganizationDomain | None:
        normalized = OrganizationDomainRepo.normalize(host)
        if not normalized:
            return None
        stmt = select(OrganizationDomain).where(
            OrganizationDomain.domain == normalized,
            OrganizationDomain.verified.is_(True),
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def delete(db: AsyncSession, domain_id: uuid.UUID) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(OrganizationDomain).where(OrganizationDomain.id == domain_id)
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def set_primary(
        db: AsyncSession, *, org_id: uuid.UUID, domain_id: uuid.UUID
    ) -> OrganizationDomain | None:
        # Clear existing primary, then set the chosen one.
        await db.execute(
            update(OrganizationDomain)
            .where(OrganizationDomain.org_id == org_id)
            .values(is_primary=False)
        )
        result = await db.execute(
            update(OrganizationDomain)
            .where(
                OrganizationDomain.id == domain_id,
                OrganizationDomain.org_id == org_id,
            )
            .values(is_primary=True)
        )
        if not result.rowcount:
            return None
        await db.flush()
        return await OrganizationDomainRepo.get_by_id(db, domain_id)


class OrgSSOConfigRepo:
    @staticmethod
    async def get_for_org(db: AsyncSession, org_id: uuid.UUID) -> OrgSSOConfig | None:
        stmt = select(OrgSSOConfig).where(OrgSSOConfig.org_id == org_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        provider: str,
        discovery_url: str,
        client_id: str,
        client_secret_encrypted: str,
        is_active: bool = True,
        scopes: str = "openid email profile",
        email_claim: str = "email",
        name_claim: str = "name",
        default_role: str = "viewer",
        allowed_email_domains: str | None = None,
    ) -> OrgSSOConfig:
        existing = await OrgSSOConfigRepo.get_for_org(db, org_id)
        if existing is None:
            row = OrgSSOConfig(
                org_id=org_id,
                provider=provider,
                discovery_url=discovery_url,
                client_id=client_id,
                client_secret_encrypted=client_secret_encrypted,
                is_active=is_active,
                scopes=scopes,
                email_claim=email_claim,
                name_claim=name_claim,
                default_role=default_role,
                allowed_email_domains=allowed_email_domains,
            )
            db.add(row)
            await db.flush()
            return row

        existing.provider = provider
        existing.discovery_url = discovery_url
        existing.client_id = client_id
        # Only overwrite the encrypted secret when caller actually passed a new one.
        if client_secret_encrypted:
            existing.client_secret_encrypted = client_secret_encrypted
        existing.is_active = is_active
        existing.scopes = scopes
        existing.email_claim = email_claim
        existing.name_claim = name_claim
        existing.default_role = default_role
        existing.allowed_email_domains = allowed_email_domains
        await db.flush()
        return existing

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(OrgSSOConfig).where(OrgSSOConfig.org_id == org_id)
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0


class OrgSAMLConfigRepo:
    """Per-org SAML 2.0 SP configuration (Sprint 30)."""

    @staticmethod
    async def get_for_org(db: AsyncSession, org_id: uuid.UUID) -> OrgSAMLConfig | None:
        stmt = select(OrgSAMLConfig).where(OrgSAMLConfig.org_id == org_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        idp_metadata_url: str | None = None,
        idp_metadata_xml: str | None = None,
        is_active: bool = True,
        email_attribute: str = (
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
        ),
        name_attribute: str = (
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
        ),
        default_role: str = "viewer",
        allowed_email_domains: str | None = None,
        want_assertions_signed: bool = True,
        want_response_signed: bool = True,
    ) -> OrgSAMLConfig:
        if bool(idp_metadata_url) == bool(idp_metadata_xml):
            raise ValueError(
                "Provide exactly one of idp_metadata_url or idp_metadata_xml"
            )

        existing = await OrgSAMLConfigRepo.get_for_org(db, org_id)
        if existing is None:
            row = OrgSAMLConfig(
                org_id=org_id,
                idp_metadata_url=idp_metadata_url,
                idp_metadata_xml=idp_metadata_xml,
                is_active=is_active,
                email_attribute=email_attribute,
                name_attribute=name_attribute,
                default_role=default_role,
                allowed_email_domains=allowed_email_domains,
                want_assertions_signed=want_assertions_signed,
                want_response_signed=want_response_signed,
            )
            db.add(row)
            await db.flush()
            return row

        existing.idp_metadata_url = idp_metadata_url
        existing.idp_metadata_xml = idp_metadata_xml
        existing.is_active = is_active
        existing.email_attribute = email_attribute
        existing.name_attribute = name_attribute
        existing.default_role = default_role
        existing.allowed_email_domains = allowed_email_domains
        existing.want_assertions_signed = want_assertions_signed
        existing.want_response_signed = want_response_signed
        await db.flush()
        return existing

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(OrgSAMLConfig).where(OrgSAMLConfig.org_id == org_id)
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Auditor (Sprint 32)
# ---------------------------------------------------------------------------


class AuditRunRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        analyzers: list[str],
        created_by: uuid.UUID | None = None,
        status: str = "queued",
    ) -> AuditRun:
        run = AuditRun(
            org_id=org_id,
            analyzers=list(analyzers),
            status=status,
            created_by=created_by,
        )
        db.add(run)
        await db.flush()
        return run

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID
    ) -> AuditRun | None:
        return (
            await db.execute(
                select(AuditRun).where(AuditRun.id == run_id, AuditRun.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[AuditRun]:
        stmt = (
            select(AuditRun)
            .where(AuditRun.org_id == org_id)
            .order_by(AuditRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update_status(
        db: AsyncSession,
        org_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        finding_count: int | None = None,
        error: str | None = None,
    ) -> AuditRun | None:
        values: dict[str, Any] = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        if finding_count is not None:
            values["finding_count"] = finding_count
        if error is not None:
            values["error"] = error
        stmt = (
            update(AuditRun)
            .where(AuditRun.org_id == org_id, AuditRun.id == run_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await AuditRunRepo.get_by_id(db, org_id, run_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> bool:
        run = await AuditRunRepo.get_by_id(db, org_id, run_id)
        if run is None:
            return False
        await db.delete(run)
        await db.flush()
        return True


class AuditScheduleRepo:
    """Sprint 39 step 2 — scheduled audit runs."""

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        analyzers: list[str],
        interval_minutes: int,
        next_run_at: datetime,
        description: str | None = None,
        mcp_server_name: str | None = None,
        focus_areas: list[str] | None = None,
        is_active: bool = True,
        created_by: uuid.UUID | None = None,
    ) -> AuditSchedule:
        row = AuditSchedule(
            org_id=org_id,
            name=name,
            description=description,
            analyzers=list(analyzers),
            mcp_server_name=mcp_server_name,
            focus_areas=list(focus_areas or []),
            interval_minutes=interval_minutes,
            is_active=is_active,
            next_run_at=next_run_at,
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, schedule_id: uuid.UUID
    ) -> AuditSchedule | None:
        stmt = select(AuditSchedule).where(
            AuditSchedule.id == schedule_id, AuditSchedule.org_id == org_id
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_for_org(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[AuditSchedule]:
        stmt = (
            select(AuditSchedule)
            .where(AuditSchedule.org_id == org_id)
            .order_by(AuditSchedule.created_at.desc())
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def list_due(
        db: AsyncSession, *, now: datetime
    ) -> Sequence[AuditSchedule]:
        """Return active schedules whose ``next_run_at`` is in the past."""

        stmt = (
            select(AuditSchedule)
            .where(AuditSchedule.is_active.is_(True))
            .where(AuditSchedule.next_run_at <= now)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def mark_run(
        db: AsyncSession,
        schedule: AuditSchedule,
        *,
        now: datetime,
    ) -> None:
        """Advance ``last_run_at`` to ``now`` and push ``next_run_at`` by
        ``interval_minutes``. Idempotent — never moves backwards."""

        schedule.last_run_at = now
        schedule.next_run_at = now + timedelta(minutes=schedule.interval_minutes)
        await db.flush()

    @staticmethod
    async def update(
        db: AsyncSession,
        schedule: AuditSchedule,
        *,
        name: str | None = None,
        description: str | None = None,
        analyzers: list[str] | None = None,
        mcp_server_name: str | None = None,
        focus_areas: list[str] | None = None,
        interval_minutes: int | None = None,
        is_active: bool | None = None,
    ) -> AuditSchedule:
        if name is not None:
            schedule.name = name
        if description is not None:
            schedule.description = description
        if analyzers is not None:
            schedule.analyzers = list(analyzers)
        if mcp_server_name is not None:
            schedule.mcp_server_name = mcp_server_name
        if focus_areas is not None:
            schedule.focus_areas = list(focus_areas)
        if interval_minutes is not None:
            schedule.interval_minutes = interval_minutes
        if is_active is not None:
            schedule.is_active = is_active
        await db.flush()
        return schedule

    @staticmethod
    async def delete(
        db: AsyncSession, schedule: AuditSchedule
    ) -> None:
        await db.delete(schedule)
        await db.flush()


class AuditFindingRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        run_id: uuid.UUID,
        analyzer: str,
        severity: str,
        message: str,
        category: str | None = None,
        resource: str | None = None,
        suggested_fix: str | None = None,
        status: str = "open",
    ) -> AuditFinding:
        row = AuditFinding(
            org_id=org_id,
            run_id=run_id,
            analyzer=analyzer,
            severity=severity,
            message=message,
            category=category,
            resource=resource,
            suggested_fix=suggested_fix,
            status=status,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, finding_id: uuid.UUID
    ) -> AuditFinding | None:
        return (
            await db.execute(
                select(AuditFinding).where(
                    AuditFinding.id == finding_id,
                    AuditFinding.org_id == org_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_by_run(
        db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID
    ) -> Sequence[AuditFinding]:
        stmt = (
            select(AuditFinding)
            .where(AuditFinding.org_id == org_id, AuditFinding.run_id == run_id)
            .order_by(AuditFinding.created_at.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def list_filtered(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        status: str | None = None,
        severity: str | None = None,
        analyzer: str | None = None,
        run_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AuditFinding]:
        stmt = select(AuditFinding).where(AuditFinding.org_id == org_id)
        if status is not None:
            stmt = stmt.where(AuditFinding.status == status)
        if severity is not None:
            stmt = stmt.where(AuditFinding.severity == severity)
        if analyzer is not None:
            stmt = stmt.where(AuditFinding.analyzer == analyzer)
        if run_id is not None:
            stmt = stmt.where(AuditFinding.run_id == run_id)
        stmt = stmt.order_by(AuditFinding.created_at.desc()).limit(limit).offset(offset)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update_status(
        db: AsyncSession,
        org_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        status: str,
        session_id: uuid.UUID | None = None,
        session_id_provided: bool = False,
        dismiss_reason: str | None = None,
    ) -> AuditFinding | None:
        values: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if session_id_provided:
            values["session_id"] = session_id
        if dismiss_reason is not None:
            values["dismiss_reason"] = dismiss_reason
        stmt = (
            update(AuditFinding)
            .where(
                AuditFinding.org_id == org_id,
                AuditFinding.id == finding_id,
            )
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await AuditFindingRepo.get_by_id(db, org_id, finding_id)


# ---------------------------------------------------------------------------
# Paging (Sprint 33)
# ---------------------------------------------------------------------------


class TeamRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        slug: str,
        description: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Team:
        team = Team(
            org_id=org_id,
            name=name,
            slug=slug,
            description=description,
            created_by=created_by,
        )
        db.add(team)
        await db.flush()
        return team

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, team_id: uuid.UUID
    ) -> Team | None:
        return (
            await db.execute(
                select(Team).where(Team.id == team_id, Team.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(db: AsyncSession, org_id: uuid.UUID) -> Sequence[Team]:
        stmt = select(Team).where(Team.org_id == org_id).order_by(Team.name)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        description_provided: bool = False,
    ) -> Team | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if description_provided:
            values["description"] = description
        if not values:
            return await TeamRepo.get_by_id(db, org_id, team_id)
        stmt = (
            update(Team)
            .where(Team.org_id == org_id, Team.id == team_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await TeamRepo.get_by_id(db, org_id, team_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, team_id: uuid.UUID) -> bool:
        team = await TeamRepo.get_by_id(db, org_id, team_id)
        if team is None:
            return False
        await db.delete(team)
        await db.flush()
        return True

    @staticmethod
    async def add_member(
        db: AsyncSession,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        role: str = "member",
    ) -> TeamMember:
        member = TeamMember(org_id=org_id, team_id=team_id, user_id=user_id, role=role)
        db.add(member)
        await db.flush()
        return member

    @staticmethod
    async def remove_member(
        db: AsyncSession,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(TeamMember).where(
            TeamMember.org_id == org_id,
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def list_members(
        db: AsyncSession, org_id: uuid.UUID, team_id: uuid.UUID
    ) -> Sequence[TeamMember]:
        stmt = (
            select(TeamMember)
            .where(TeamMember.org_id == org_id, TeamMember.team_id == team_id)
            .order_by(TeamMember.added_at)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def is_member(
        db: AsyncSession,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """True when ``user_id`` is on ``team_id`` within ``org_id``."""
        stmt = select(TeamMember.id).where(
            TeamMember.org_id == org_id,
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
        return (await db.execute(stmt)).first() is not None


class ServiceRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID,
        name: str,
        slug: str,
        description: str | None = None,
        priority: str = "P2",
        intake_token: str | None = None,
        preferred_mcp_server_ids: list[str] | None = None,
        preferred_model_config_ids: list[str] | None = None,
        ai_default_tier: int | None = None,
        external_refs: dict | None = None,
        is_active: bool = True,
    ) -> Service:
        service = Service(
            org_id=org_id,
            team_id=team_id,
            name=name,
            slug=slug,
            description=description,
            priority=priority,
            intake_token=intake_token,
            preferred_mcp_server_ids=preferred_mcp_server_ids or [],
            preferred_model_config_ids=preferred_model_config_ids or [],
            ai_default_tier=ai_default_tier,
            external_refs=external_refs,
            is_active=is_active,
        )
        db.add(service)
        await db.flush()
        return service

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> Service | None:
        return (
            await db.execute(
                select(Service).where(
                    Service.id == service_id, Service.org_id == org_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_slug(
        db: AsyncSession, org_id: uuid.UUID, slug: str
    ) -> Service | None:
        return (
            await db.execute(
                select(Service).where(Service.org_id == org_id, Service.slug == slug)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_intake_token(
        db: AsyncSession, intake_token: str
    ) -> Service | None:
        return (
            await db.execute(
                select(Service).where(Service.intake_token == intake_token)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None = None,
    ) -> Sequence[Service]:
        stmt = select(Service).where(Service.org_id == org_id)
        if team_id is not None:
            stmt = stmt.where(Service.team_id == team_id)
        stmt = stmt.order_by(Service.name)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        service_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None = None,
        name: str | None = None,
        description: str | None = None,
        description_provided: bool = False,
        priority: str | None = None,
        intake_token: str | None = None,
        preferred_mcp_server_ids: list[str] | None = None,
        preferred_mcp_server_ids_provided: bool = False,
        preferred_model_config_ids: list[str] | None = None,
        preferred_model_config_ids_provided: bool = False,
        ai_default_tier: int | None = None,
        ai_default_tier_provided: bool = False,
        external_refs: dict | None = None,
        external_refs_provided: bool = False,
        is_active: bool | None = None,
    ) -> Service | None:
        values: dict[str, Any] = {}
        if team_id is not None:
            values["team_id"] = team_id
        if name is not None:
            values["name"] = name
        if description_provided:
            values["description"] = description
        if priority is not None:
            values["priority"] = priority
        if intake_token is not None:
            values["intake_token"] = intake_token
        if preferred_mcp_server_ids_provided:
            values["preferred_mcp_server_ids"] = preferred_mcp_server_ids or []
        if preferred_model_config_ids_provided:
            values["preferred_model_config_ids"] = preferred_model_config_ids or []
        if ai_default_tier_provided:
            values["ai_default_tier"] = ai_default_tier
        if external_refs_provided:
            values["external_refs"] = external_refs
        if is_active is not None:
            values["is_active"] = is_active
        if not values:
            return await ServiceRepo.get_by_id(db, org_id, service_id)
        stmt = (
            update(Service)
            .where(Service.org_id == org_id, Service.id == service_id)
            .values(**values)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await ServiceRepo.get_by_id(db, org_id, service_id)

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> bool:
        svc = await ServiceRepo.get_by_id(db, org_id, service_id)
        if svc is None:
            return False
        await db.delete(svc)
        await db.flush()
        return True

    @staticmethod
    async def attach_roster(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        service_id: uuid.UUID,
        roster_id: uuid.UUID,
        level: int = 1,
    ) -> ServiceRoster:
        link = ServiceRoster(
            org_id=org_id,
            service_id=service_id,
            roster_id=roster_id,
            level=level,
        )
        db.add(link)
        await db.flush()
        return link

    @staticmethod
    async def list_rosters_for_service(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> Sequence[ServiceRoster]:
        stmt = (
            select(ServiceRoster)
            .where(
                ServiceRoster.org_id == org_id,
                ServiceRoster.service_id == service_id,
            )
            .order_by(ServiceRoster.level)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def detach_roster(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        service_id: uuid.UUID,
        roster_id: uuid.UUID,
    ) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(ServiceRoster).where(
            ServiceRoster.org_id == org_id,
            ServiceRoster.service_id == service_id,
            ServiceRoster.roster_id == roster_id,
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0


class RosterRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID,
        name: str,
        anchor_date,
        description: str | None = None,
        time_zone: str = "UTC",
        pattern: str = "weekly",
        pattern_length: int = 7,
        coverage_start_time: str = "09:00",
        coverage_end_time: str = "17:00",
        handoff_time: str = "09:00",
        handoff_day: str | None = None,
        is_active: bool = True,
    ) -> Roster:
        roster = Roster(
            org_id=org_id,
            team_id=team_id,
            name=name,
            description=description,
            time_zone=time_zone,
            pattern=pattern,
            pattern_length=pattern_length,
            coverage_start_time=coverage_start_time,
            coverage_end_time=coverage_end_time,
            handoff_time=handoff_time,
            handoff_day=handoff_day,
            anchor_date=anchor_date,
            is_active=is_active,
        )
        db.add(roster)
        await db.flush()
        return roster

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, roster_id: uuid.UUID
    ) -> Roster | None:
        return (
            await db.execute(
                select(Roster).where(Roster.id == roster_id, Roster.org_id == org_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None = None,
    ) -> Sequence[Roster]:
        stmt = select(Roster).where(Roster.org_id == org_id)
        if team_id is not None:
            stmt = stmt.where(Roster.team_id == team_id)
        stmt = stmt.order_by(Roster.name)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        roster_id: uuid.UUID,
        **fields: Any,
    ) -> Roster | None:
        if not fields:
            return await RosterRepo.get_by_id(db, org_id, roster_id)
        stmt = (
            update(Roster)
            .where(Roster.org_id == org_id, Roster.id == roster_id)
            .values(**fields)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await RosterRepo.get_by_id(db, org_id, roster_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, roster_id: uuid.UUID) -> bool:
        roster = await RosterRepo.get_by_id(db, org_id, roster_id)
        if roster is None:
            return False
        await db.delete(roster)
        await db.flush()
        return True

    @staticmethod
    async def add_member(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        roster_id: uuid.UUID,
        user_id: uuid.UUID,
        position_index: int,
    ) -> RosterMember:
        m = RosterMember(
            org_id=org_id,
            roster_id=roster_id,
            user_id=user_id,
            position_index=position_index,
        )
        db.add(m)
        await db.flush()
        return m

    @staticmethod
    async def remove_user_everywhere(db: AsyncSession, user_id: uuid.UUID) -> int:
        """Delete all of a user's roster memberships (e.g. on deactivation), so
        they stop paging and don't block deletion with stale roster references.
        Returns the number of memberships removed."""
        from sqlalchemy import delete as sql_delete

        result = await db.execute(
            sql_delete(RosterMember).where(RosterMember.user_id == user_id)
        )
        await db.flush()
        return result.rowcount or 0

    @staticmethod
    async def list_members(
        db: AsyncSession,
        org_id: uuid.UUID,
        roster_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> Sequence[RosterMember]:
        """List roster members. With ``active_only`` deactivated / soft-deleted
        users are excluded — used for on-call resolution + paging so a disabled
        user never resolves as on-call or gets paged (their membership row is
        preserved for history)."""
        stmt = select(RosterMember).where(
            RosterMember.org_id == org_id,
            RosterMember.roster_id == roster_id,
        )
        if active_only:
            stmt = stmt.join(User, User.id == RosterMember.user_id).where(
                User.is_active.is_(True), User.deleted_at.is_(None)
            )
        stmt = stmt.order_by(RosterMember.position_index)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def remove_member(
        db: AsyncSession,
        org_id: uuid.UUID,
        roster_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(RosterMember).where(
            RosterMember.org_id == org_id,
            RosterMember.roster_id == roster_id,
            RosterMember.user_id == user_id,
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def reorder_members(
        db: AsyncSession,
        org_id: uuid.UUID,
        roster_id: uuid.UUID,
        *,
        ordered_user_ids: list[uuid.UUID],
    ) -> None:
        """Replace position_index for each listed user. Two-phase to avoid
        running afoul of the (roster_id, position_index) UNIQUE constraint."""
        members = await RosterRepo.list_members(db, org_id, roster_id)
        # Phase 1: move everyone to a temporary offset to free positions.
        offset = len(members) + 1
        for i, m in enumerate(members):
            m.position_index = offset + i
        await db.flush()
        # Phase 2: set the requested positions.
        index_by_user = {uid: i for i, uid in enumerate(ordered_user_ids)}
        for m in members:
            if m.user_id in index_by_user:
                m.position_index = index_by_user[m.user_id]
        await db.flush()


class RosterOverrideRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        roster_id: uuid.UUID,
        covering_user_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        reason: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> RosterOverride:
        ov = RosterOverride(
            org_id=org_id,
            roster_id=roster_id,
            covering_user_id=covering_user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            reason=reason,
            created_by=created_by,
        )
        db.add(ov)
        await db.flush()
        return ov

    @staticmethod
    async def list_for_roster(
        db: AsyncSession, org_id: uuid.UUID, roster_id: uuid.UUID
    ) -> Sequence[RosterOverride]:
        stmt = (
            select(RosterOverride)
            .where(
                RosterOverride.org_id == org_id,
                RosterOverride.roster_id == roster_id,
            )
            .order_by(RosterOverride.starts_at)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, override_id: uuid.UUID
    ) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(RosterOverride).where(
            RosterOverride.org_id == org_id, RosterOverride.id == override_id
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0


class PriorityRuleRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        name: str,
        condition: dict,
        priority: str,
        rule_index: int = 0,
        response_mode: str | None = None,
        is_active: bool = True,
    ) -> PriorityRule:
        rule = PriorityRule(
            org_id=org_id,
            name=name,
            condition=condition,
            priority=priority,
            rule_index=rule_index,
            response_mode=response_mode,
            is_active=is_active,
        )
        db.add(rule)
        await db.flush()
        return rule

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, rule_id: uuid.UUID
    ) -> PriorityRule | None:
        return (
            await db.execute(
                select(PriorityRule).where(
                    PriorityRule.id == rule_id,
                    PriorityRule.org_id == org_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        active_only: bool = False,
    ) -> Sequence[PriorityRule]:
        stmt = select(PriorityRule).where(PriorityRule.org_id == org_id)
        if active_only:
            stmt = stmt.where(PriorityRule.is_active == True)
        stmt = stmt.order_by(PriorityRule.rule_index)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
        **fields: Any,
    ) -> PriorityRule | None:
        if not fields:
            return await PriorityRuleRepo.get_by_id(db, org_id, rule_id)
        stmt = (
            update(PriorityRule)
            .where(PriorityRule.org_id == org_id, PriorityRule.id == rule_id)
            .values(**fields)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await PriorityRuleRepo.get_by_id(db, org_id, rule_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        rule = await PriorityRuleRepo.get_by_id(db, org_id, rule_id)
        if rule is None:
            return False
        await db.delete(rule)
        await db.flush()
        return True

    @staticmethod
    async def log_llm_override(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        rule_priority: str,
        llm_priority: str,
        llm_reason: str | None,
    ) -> PriorityLLMOverrideLog:
        row = PriorityLLMOverrideLog(
            org_id=org_id,
            incident_id=incident_id,
            rule_priority=rule_priority,
            llm_priority=llm_priority,
            llm_reason=llm_reason,
        )
        db.add(row)
        await db.flush()
        return row


class IncidentCommentRepo:
    """Operator notes on an incident (v1.2 event/comment UX)."""

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        body: str,
        author_user_id: uuid.UUID | None,
    ) -> IncidentComment:
        row = IncidentComment(
            org_id=org_id,
            incident_id=incident_id,
            body=body,
            author_user_id=author_user_id,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def repoint(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        from_incident_id: uuid.UUID,
        to_incident_id: uuid.UUID,
    ) -> int:
        """Move all comments from one incident to another (incident combine).

        Returns the number of comments moved so the combined timeline reads as
        one coherent story on the primary incident.
        """
        stmt = (
            update(IncidentComment)
            .where(IncidentComment.org_id == org_id)
            .where(IncidentComment.incident_id == from_incident_id)
            .values(incident_id=to_incident_id)
        )
        result = await db.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, comment_id: uuid.UUID
    ) -> IncidentComment | None:
        stmt = select(IncidentComment).where(
            IncidentComment.id == comment_id,
            IncidentComment.org_id == org_id,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[IncidentComment]:
        stmt = (
            select(IncidentComment)
            .where(
                IncidentComment.org_id == org_id,
                IncidentComment.incident_id == incident_id,
            )
            .order_by(IncidentComment.created_at)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def delete(
        db: AsyncSession, org_id: uuid.UUID, comment_id: uuid.UUID
    ) -> bool:
        row = await IncidentCommentRepo.get_by_id(db, org_id, comment_id)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True


class IncidentAssignmentRepo:
    @staticmethod
    async def get_active(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> IncidentAssignment | None:
        stmt = (
            select(IncidentAssignment)
            .where(
                IncidentAssignment.org_id == org_id,
                IncidentAssignment.incident_id == incident_id,
                IncidentAssignment.released_at.is_(None),
            )
            .order_by(IncidentAssignment.assigned_at.desc())
        )
        return (await db.execute(stmt)).scalars().first()

    @staticmethod
    async def get_active_for_incidents(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_ids: Sequence[uuid.UUID],
    ) -> dict[uuid.UUID, IncidentAssignment]:
        """Batch variant of ``get_active``: latest active assignment per incident.

        Returns a mapping of incident_id → assignment for the incidents that have
        an active (unreleased) assignment. Rows are ordered newest-first so the
        first row seen for each incident wins.
        """
        if not incident_ids:
            return {}
        stmt = (
            select(IncidentAssignment)
            .where(
                IncidentAssignment.org_id == org_id,
                IncidentAssignment.incident_id.in_(incident_ids),
                IncidentAssignment.released_at.is_(None),
            )
            .order_by(IncidentAssignment.assigned_at.desc())
        )
        result: dict[uuid.UUID, IncidentAssignment] = {}
        for row in (await db.execute(stmt)).scalars().all():
            result.setdefault(row.incident_id, row)
        return result

    @staticmethod
    async def assign(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        assigned_by: str = "manual",
    ) -> IncidentAssignment:
        existing = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
        if existing is not None:
            existing.released_at = datetime.now(timezone.utc)
            await db.flush()
        row = IncidentAssignment(
            org_id=org_id,
            incident_id=incident_id,
            assigned_to=user_id,
            assigned_by=assigned_by,
        )
        db.add(row)
        await db.flush()
        # MTTA: stamp the incident's first-acknowledgment time once. Any
        # assignment (self-ack / chain-ack / takeover) counts as the first
        # human acknowledgment; never overwrite an earlier stamp.
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is not None and incident.acknowledged_at is None:
            incident.acknowledged_at = row.assigned_at
            await db.flush()
        return row

    @staticmethod
    async def release(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> bool:
        existing = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
        if existing is None:
            return False
        existing.released_at = datetime.now(timezone.utc)
        await db.flush()
        return True

    @staticmethod
    async def list_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[IncidentAssignment]:
        stmt = (
            select(IncidentAssignment)
            .where(
                IncidentAssignment.org_id == org_id,
                IncidentAssignment.incident_id == incident_id,
            )
            .order_by(IncidentAssignment.assigned_at.desc())
        )
        return (await db.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# Escalation chains (Sprint 34)
# ---------------------------------------------------------------------------


class EscalationChainRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID,
        name: str,
        description: str | None = None,
        is_active: bool = True,
    ) -> EscalationChain:
        chain = EscalationChain(
            org_id=org_id,
            team_id=team_id,
            name=name,
            description=description,
            is_active=is_active,
        )
        db.add(chain)
        await db.flush()
        return chain

    @staticmethod
    async def get_by_id(
        db: AsyncSession, org_id: uuid.UUID, chain_id: uuid.UUID
    ) -> EscalationChain | None:
        return (
            await db.execute(
                select(EscalationChain).where(
                    EscalationChain.id == chain_id,
                    EscalationChain.org_id == org_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        team_id: uuid.UUID | None = None,
    ) -> Sequence[EscalationChain]:
        stmt = select(EscalationChain).where(EscalationChain.org_id == org_id)
        if team_id is not None:
            stmt = stmt.where(EscalationChain.team_id == team_id)
        stmt = stmt.order_by(EscalationChain.name)
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        org_id: uuid.UUID,
        chain_id: uuid.UUID,
        **fields: Any,
    ) -> EscalationChain | None:
        if not fields:
            return await EscalationChainRepo.get_by_id(db, org_id, chain_id)
        stmt = (
            update(EscalationChain)
            .where(EscalationChain.org_id == org_id, EscalationChain.id == chain_id)
            .values(**fields)
        )
        result = await db.execute(stmt)
        if not result.rowcount:
            return None
        await db.flush()
        return await EscalationChainRepo.get_by_id(db, org_id, chain_id)

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, chain_id: uuid.UUID) -> bool:
        chain = await EscalationChainRepo.get_by_id(db, org_id, chain_id)
        if chain is None:
            return False
        await db.delete(chain)
        await db.flush()
        return True


class EscalationStepRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        chain_id: uuid.UUID,
        step_index: int,
        target_type: str,
        target_id: uuid.UUID,
        timeout_seconds: int = 300,
        notify_channels: dict | None = None,
    ) -> EscalationStep:
        step = EscalationStep(
            org_id=org_id,
            chain_id=chain_id,
            step_index=step_index,
            target_type=target_type,
            target_id=target_id,
            timeout_seconds=timeout_seconds,
            notify_channels=notify_channels,
        )
        db.add(step)
        await db.flush()
        return step

    @staticmethod
    async def list_for_chain(
        db: AsyncSession, org_id: uuid.UUID, chain_id: uuid.UUID
    ) -> Sequence[EscalationStep]:
        stmt = (
            select(EscalationStep)
            .where(
                EscalationStep.org_id == org_id,
                EscalationStep.chain_id == chain_id,
            )
            .order_by(EscalationStep.step_index)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def delete(db: AsyncSession, org_id: uuid.UUID, step_id: uuid.UUID) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(EscalationStep).where(
            EscalationStep.org_id == org_id, EscalationStep.id == step_id
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0

    @staticmethod
    async def update_fields(
        db: AsyncSession,
        org_id: uuid.UUID,
        step_id: uuid.UUID,
        *,
        timeout_seconds: int | None = None,
        notify_channels: dict | None = None,
        notify_channels_set: bool = False,
    ) -> EscalationStep | None:
        """Partial update of an escalation step. Sprint 49.

        ``notify_channels_set`` lets a caller explicitly null the channels
        map (otherwise omitting the field leaves it untouched).
        """
        from sqlalchemy import update as sql_update

        values: dict = {}
        if timeout_seconds is not None:
            values["timeout_seconds"] = timeout_seconds
        if notify_channels_set:
            values["notify_channels"] = notify_channels
        if not values:
            stmt = select(EscalationStep).where(
                EscalationStep.org_id == org_id,
                EscalationStep.id == step_id,
            )
            return (await db.execute(stmt)).scalar_one_or_none()
        stmt = (
            sql_update(EscalationStep)
            .where(
                EscalationStep.org_id == org_id,
                EscalationStep.id == step_id,
            )
            .values(**values)
            .returning(EscalationStep)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        await db.flush()
        return row

    @staticmethod
    async def reorder(
        db: AsyncSession,
        org_id: uuid.UUID,
        chain_id: uuid.UUID,
        ordered_step_ids: list[uuid.UUID],
    ) -> Sequence[EscalationStep]:
        """Bulk reindex steps in a chain. Sprint 49.

        Two-pass write to avoid the (chain_id, step_index) unique-index
        collision when reordering: first bump every targeted step into a
        scratch range (negative indices), then assign the final indices.
        """
        from sqlalchemy import update as sql_update

        if not ordered_step_ids:
            return []

        # Phase 1 — park all targeted steps in negative indices keyed by the
        # incoming order, so any subsequent index assignment is collision-free.
        for offset, step_id in enumerate(ordered_step_ids):
            await db.execute(
                sql_update(EscalationStep)
                .where(
                    EscalationStep.org_id == org_id,
                    EscalationStep.chain_id == chain_id,
                    EscalationStep.id == step_id,
                )
                .values(step_index=-1 - offset)
            )

        # Phase 2 — assign final indices.
        for index, step_id in enumerate(ordered_step_ids):
            await db.execute(
                sql_update(EscalationStep)
                .where(
                    EscalationStep.org_id == org_id,
                    EscalationStep.chain_id == chain_id,
                    EscalationStep.id == step_id,
                )
                .values(step_index=index)
            )
        await db.flush()
        return await EscalationStepRepo.list_for_chain(db, org_id, chain_id)


class ServiceEscalationChainRepo:
    @staticmethod
    async def link(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        service_id: uuid.UUID,
        chain_id: uuid.UUID,
        applies_when: dict | None = None,
    ) -> ServiceEscalationChain:
        row = ServiceEscalationChain(
            org_id=org_id,
            service_id=service_id,
            chain_id=chain_id,
            applies_when=applies_when,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_for_service(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> Sequence[ServiceEscalationChain]:
        stmt = select(ServiceEscalationChain).where(
            ServiceEscalationChain.org_id == org_id,
            ServiceEscalationChain.service_id == service_id,
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def list_for_chain(
        db: AsyncSession, org_id: uuid.UUID, chain_id: uuid.UUID
    ) -> Sequence[ServiceEscalationChain]:
        """Sprint 49 — power the chain editor's "Where used" panel."""
        stmt = select(ServiceEscalationChain).where(
            ServiceEscalationChain.org_id == org_id,
            ServiceEscalationChain.chain_id == chain_id,
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def unlink(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        service_id: uuid.UUID,
        chain_id: uuid.UUID,
    ) -> bool:
        from sqlalchemy import delete as sql_delete

        stmt = sql_delete(ServiceEscalationChain).where(
            ServiceEscalationChain.org_id == org_id,
            ServiceEscalationChain.service_id == service_id,
            ServiceEscalationChain.chain_id == chain_id,
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount > 0


class IncidentPageRepo:
    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        chain_id: uuid.UUID | None = None,
        step_index: int | None = None,
        channel: str = "recorded",
        delivery_status: str = "recorded",
        delivery_error: str | None = None,
    ) -> IncidentPage:
        page = IncidentPage(
            org_id=org_id,
            incident_id=incident_id,
            user_id=user_id,
            chain_id=chain_id,
            step_index=step_index,
            channel=channel,
            delivery_status=delivery_status,
            delivery_error=delivery_error,
        )
        db.add(page)
        await db.flush()
        return page

    @staticmethod
    async def already_paged(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        step_index: int,
    ) -> bool:
        stmt = select(IncidentPage).where(
            IncidentPage.org_id == org_id,
            IncidentPage.incident_id == incident_id,
            IncidentPage.user_id == user_id,
            IncidentPage.step_index == step_index,
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    async def list_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[IncidentPage]:
        stmt = (
            select(IncidentPage)
            .where(
                IncidentPage.org_id == org_id,
                IncidentPage.incident_id == incident_id,
            )
            .order_by(IncidentPage.sent_at)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def list_for_incidents(
        db: AsyncSession,
        org_id: uuid.UUID,
        incident_ids: Sequence[uuid.UUID],
    ) -> dict[uuid.UUID, list[IncidentPage]]:
        """Batch variant of ``list_for_incident``: pages grouped by incident.

        Each incident's pages are ordered by ``sent_at`` (oldest-first), matching
        ``list_for_incident`` so ``responder`` resolution sees the latest page last.
        """
        if not incident_ids:
            return {}
        stmt = (
            select(IncidentPage)
            .where(
                IncidentPage.org_id == org_id,
                IncidentPage.incident_id.in_(incident_ids),
            )
            .order_by(IncidentPage.sent_at)
        )
        grouped: dict[uuid.UUID, list[IncidentPage]] = {}
        for row in (await db.execute(stmt)).scalars().all():
            grouped.setdefault(row.incident_id, []).append(row)
        return grouped

    @staticmethod
    async def has_recent_delivery(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        channel: str,
        after: datetime,
    ) -> bool:
        """Sprint 35 dedup: was this (incident, user, channel) already
        delivered (or attempted with non-skipped status) since ``after``?"""

        stmt = select(IncidentPage).where(
            IncidentPage.org_id == org_id,
            IncidentPage.incident_id == incident_id,
            IncidentPage.user_id == user_id,
            IncidentPage.channel == channel,
            IncidentPage.delivery_status.in_(("sent", "failed")),
            IncidentPage.sent_at >= after,
        )
        return (await db.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    async def ack_all_unacked(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        via: str,
    ) -> int:
        """Stamp ack_at/ack_via on every unacked page for (incident, user).

        Returns the number of rows updated.
        """

        now = datetime.now(timezone.utc)
        stmt = (
            update(IncidentPage)
            .where(
                IncidentPage.org_id == org_id,
                IncidentPage.incident_id == incident_id,
                IncidentPage.user_id == user_id,
                IncidentPage.ack_at.is_(None),
            )
            .values(ack_at=now, ack_via=via)
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.rowcount or 0


class IncidentChainStateRepo:
    @staticmethod
    async def get_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> IncidentChainState | None:
        return (
            await db.execute(
                select(IncidentChainState).where(
                    IncidentChainState.org_id == org_id,
                    IncidentChainState.incident_id == incident_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        chain_id: uuid.UUID,
    ) -> IncidentChainState:
        state = IncidentChainState(
            org_id=org_id, incident_id=incident_id, chain_id=chain_id
        )
        db.add(state)
        await db.flush()
        return state

    @staticmethod
    async def list_due(
        db: AsyncSession, *, now: datetime
    ) -> Sequence[IncidentChainState]:
        """Return states whose next_step_due_at has passed (any org)."""

        stmt = (
            select(IncidentChainState)
            .where(IncidentChainState.status == "running")
            .where(IncidentChainState.next_step_due_at.is_not(None))
            .where(IncidentChainState.next_step_due_at <= now)
        )
        return (await db.execute(stmt)).scalars().all()


class NotificationEscalationRepo:
    """Per-(incident, user) staged notification escalation state."""

    @staticmethod
    async def get(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> NotificationEscalation | None:
        return (
            await db.execute(
                select(NotificationEscalation).where(
                    NotificationEscalation.org_id == org_id,
                    NotificationEscalation.incident_id == incident_id,
                    NotificationEscalation.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        incident_id: uuid.UUID,
        user_id: uuid.UUID,
        priority: str | None,
        stages: list,
    ) -> NotificationEscalation:
        state = NotificationEscalation(
            org_id=org_id,
            incident_id=incident_id,
            user_id=user_id,
            priority=priority,
            stages=stages,
        )
        db.add(state)
        await db.flush()
        return state

    @staticmethod
    async def list_due(
        db: AsyncSession, *, now: datetime
    ) -> Sequence[NotificationEscalation]:
        """Running escalations whose next stage is due (any org)."""
        stmt = (
            select(NotificationEscalation)
            .where(NotificationEscalation.status == "running")
            .where(NotificationEscalation.next_stage_due_at.is_not(None))
            .where(NotificationEscalation.next_stage_due_at <= now)
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def list_running_for_incident(
        db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
    ) -> Sequence[NotificationEscalation]:
        stmt = (
            select(NotificationEscalation)
            .where(NotificationEscalation.org_id == org_id)
            .where(NotificationEscalation.incident_id == incident_id)
            .where(NotificationEscalation.status == "running")
        )
        return (await db.execute(stmt)).scalars().all()


class IncidentMemoryRepo:
    """Sprint 45 — AI incident memory.

    All methods are org-scoped; callers must pass `org_id` and the repo
    enforces the boundary on every read and write.
    """

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        title: str,
        summary_md: str,
        service_id: uuid.UUID | None = None,
        source_incident_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        created_by_user_id: uuid.UUID | None = None,
        # Default approved: direct/operator-authored creates are trusted. The AI
        # writeback path explicitly passes "pending" so machine-written memories
        # require human review before they are recalled.
        review_status: str = "approved",
    ) -> IncidentMemory:
        row = IncidentMemory(
            org_id=org_id,
            service_id=service_id,
            source_incident_id=source_incident_id,
            title=title,
            summary_md=summary_md,
            tags=list(tags or []),
            created_by_user_id=created_by_user_id,
            review_status=review_status,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def get_by_id(
        db: AsyncSession, memory_id: uuid.UUID, org_id: uuid.UUID
    ) -> IncidentMemory | None:
        stmt = (
            select(IncidentMemory)
            .where(IncidentMemory.id == memory_id)
            .where(IncidentMemory.org_id == org_id)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def list_for_org(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        service_id: uuid.UUID | None = None,
        include_hidden: bool = False,
        review_status: str | None = None,
    ) -> Sequence[IncidentMemory]:
        stmt = select(IncidentMemory).where(IncidentMemory.org_id == org_id)
        if service_id is not None:
            stmt = stmt.where(IncidentMemory.service_id == service_id)
        if not include_hidden:
            stmt = stmt.where(IncidentMemory.is_hidden.is_(False))
        if review_status is not None:
            stmt = stmt.where(IncidentMemory.review_status == review_status)
        stmt = stmt.order_by(IncidentMemory.updated_at.desc())
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def count_for_service(
        db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(IncidentMemory)
            .where(IncidentMemory.org_id == org_id)
            .where(IncidentMemory.service_id == service_id)
            .where(IncidentMemory.is_hidden.is_(False))
            # Only approved memories count toward the compaction cap — pending
            # ones await human review and must not be auto-deduplicated.
            .where(IncidentMemory.review_status == "approved")
        )
        return int((await db.execute(stmt)).scalar() or 0)

    @staticmethod
    async def find_relevant(
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        service_id: uuid.UUID | None,
        query: str | None,
        tags: list[str] | None,
        limit: int = 5,
    ) -> list[tuple[IncidentMemory, float]]:
        """Return top-K (memory, score) for the given service + free-text query.

        Composite score:
          - 2.0 if service_id matches exactly (or memory has no service)
          - 1.0 * number of tag overlaps
          - 0.5 if query keyword matches title or summary (ILIKE)
          - plus helpful_ratio_boost = helpful / (helpful + unhelpful + 1)
        """
        stmt = (
            select(IncidentMemory)
            .where(IncidentMemory.org_id == org_id)
            .where(IncidentMemory.is_hidden.is_(False))
            # Human-review gate: only approved memories are recalled into AI
            # sessions. Pending (awaiting review) and rejected memories never
            # surface as agent context.
            .where(IncidentMemory.review_status == "approved")
        )
        rows = (await db.execute(stmt)).scalars().all()

        normalized_query = (query or "").strip().lower()
        normalized_tags = {t.lower() for t in (tags or []) if t}

        scored: list[tuple[IncidentMemory, float]] = []
        for row in rows:
            score = 0.0
            if service_id is not None and row.service_id == service_id:
                score += 2.0
            elif row.service_id is None:
                # Global memories (no service binding) get a small lift so they
                # surface for fresh services that have no service-bound memory yet.
                score += 0.5

            if normalized_tags and row.tags:
                row_tags = {str(t).lower() for t in row.tags}
                overlap = len(row_tags & normalized_tags)
                if overlap:
                    score += float(overlap)

            if normalized_query:
                hay = f"{row.title}\n{row.summary_md}".lower()
                if normalized_query in hay:
                    score += 0.5
                else:
                    # Token-level match: any whole word from the query landing
                    # in title/summary counts as a partial hit.
                    tokens = [
                        t for t in normalized_query.split() if len(t) >= 3
                    ]
                    if any(t in hay for t in tokens):
                        score += 0.25

            helpful_ratio = row.helpful_count / float(
                row.helpful_count + row.unhelpful_count + 1
            )
            score += helpful_ratio

            if score > 0:
                scored.append((row, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    @staticmethod
    async def touch_last_used(
        db: AsyncSession, memory_id: uuid.UUID
    ) -> None:
        await db.execute(
            update(IncidentMemory)
            .where(IncidentMemory.id == memory_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )

    @staticmethod
    async def record_feedback(
        db: AsyncSession,
        *,
        memory_id: uuid.UUID,
        org_id: uuid.UUID,
        helpful: bool,
    ) -> IncidentMemory | None:
        row = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if row is None:
            return None
        if helpful:
            row.helpful_count = row.helpful_count + 1
        else:
            row.unhelpful_count = row.unhelpful_count + 1
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return row

    @staticmethod
    async def set_hidden(
        db: AsyncSession,
        *,
        memory_id: uuid.UUID,
        org_id: uuid.UUID,
        hidden: bool,
    ) -> IncidentMemory | None:
        row = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if row is None:
            return None
        row.is_hidden = hidden
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return row

    @staticmethod
    async def set_review_status(
        db: AsyncSession,
        *,
        memory_id: uuid.UUID,
        org_id: uuid.UUID,
        review_status: str,
        reviewed_by_user_id: uuid.UUID | None,
    ) -> IncidentMemory | None:
        row = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        row.review_status = review_status
        row.reviewed_by_user_id = reviewed_by_user_id
        row.reviewed_at = now
        row.updated_at = now
        await db.flush()
        return row

    @staticmethod
    async def update(
        db: AsyncSession,
        *,
        memory_id: uuid.UUID,
        org_id: uuid.UUID,
        title: str | None = None,
        summary_md: str | None = None,
        tags: list[str] | None = None,
        service_id: uuid.UUID | None = None,
        service_id_set: bool = False,
    ) -> IncidentMemory | None:
        row = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if summary_md is not None:
            row.summary_md = summary_md
        if tags is not None:
            row.tags = list(tags)
        if service_id_set:
            row.service_id = service_id
        row.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return row

    @staticmethod
    async def delete(
        db: AsyncSession, *, memory_id: uuid.UUID, org_id: uuid.UUID
    ) -> bool:
        row = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if row is None:
            return False
        await db.delete(row)
        await db.flush()
        return True


class IncidentMemoryRecallLogRepo:
    @staticmethod
    async def record(
        db: AsyncSession,
        *,
        memory_id: uuid.UUID,
        session_id: uuid.UUID,
        score: float | None = None,
    ) -> IncidentMemoryRecallLog:
        row = IncidentMemoryRecallLog(
            memory_id=memory_id,
            session_id=session_id,
            score=score,
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_for_session(
        db: AsyncSession, session_id: uuid.UUID
    ) -> Sequence[IncidentMemoryRecallLog]:
        stmt = (
            select(IncidentMemoryRecallLog)
            .where(IncidentMemoryRecallLog.session_id == session_id)
            .order_by(IncidentMemoryRecallLog.surfaced_at.asc())
        )
        return (await db.execute(stmt)).scalars().all()


# ---------------------------------------------------------------------------
# Data retention (Sprint 53)
# ---------------------------------------------------------------------------


# Default TTL applied when no per-org row exists. Operators can override
# per-category via the Config → "Storage & retention" UI or disable a category
# entirely by setting ttl_days to NULL.
DEFAULT_RETENTION_TTL_DAYS = 90

RETENTION_CATEGORIES = (
    "audit_entries",
    "ingest_log",
    "incident_memory_recall_log",
    "bot_action_audit",
)


class RetentionConfigRepo:
    """Per-org per-category retention windows for high-volume log tables."""

    @staticmethod
    async def list_for_org(
        db: AsyncSession, org_id: uuid.UUID
    ) -> Sequence[RetentionConfig]:
        stmt = (
            select(RetentionConfig)
            .where(RetentionConfig.org_id == org_id)
            .order_by(RetentionConfig.category.asc())
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def get(
        db: AsyncSession, org_id: uuid.UUID, category: str
    ) -> RetentionConfig | None:
        stmt = select(RetentionConfig).where(
            RetentionConfig.org_id == org_id,
            RetentionConfig.category == category,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        category: str,
        ttl_days: int | None,
        updated_by_user_id: uuid.UUID | None = None,
    ) -> RetentionConfig:
        """Create or update the (org, category) row.

        ``ttl_days = None`` disables the pruner for this category (operator
        explicit opt-out). Otherwise ``ttl_days`` must be >= 1.
        """
        if category not in RETENTION_CATEGORIES:
            raise ValueError(f"Unknown retention category: {category}")
        if ttl_days is not None and ttl_days < 1:
            raise ValueError("ttl_days must be NULL (disabled) or >= 1")

        existing = await RetentionConfigRepo.get(db, org_id, category)
        now = datetime.now(timezone.utc)
        if existing is None:
            row = RetentionConfig(
                org_id=org_id,
                category=category,
                ttl_days=ttl_days,
                updated_by_user_id=updated_by_user_id,
            )
            db.add(row)
            await db.flush()
            return row
        existing.ttl_days = ttl_days
        existing.updated_at = now
        existing.updated_by_user_id = updated_by_user_id
        await db.flush()
        return existing

    @staticmethod
    async def effective_ttl_days(
        db: AsyncSession, org_id: uuid.UUID, category: str
    ) -> int | None:
        """Resolved TTL for the (org, category) pair.

        Returns the stored TTL when a row exists (which may be NULL =
        disabled). Falls back to ``DEFAULT_RETENTION_TTL_DAYS`` when no row
        exists so a fresh org auto-prunes from day one without operator
        action.
        """
        existing = await RetentionConfigRepo.get(db, org_id, category)
        if existing is None:
            return DEFAULT_RETENTION_TTL_DAYS
        return existing.ttl_days

    @staticmethod
    async def stamp_run(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        category: str,
        deleted_count: int,
    ) -> None:
        """Record the last pruner outcome. Creates the row if it doesn't
        exist so the operator can see the default applied + last result even
        before they explicitly configure the category."""
        existing = await RetentionConfigRepo.get(db, org_id, category)
        now = datetime.now(timezone.utc)
        if existing is None:
            row = RetentionConfig(
                org_id=org_id,
                category=category,
                ttl_days=DEFAULT_RETENTION_TTL_DAYS,
                last_pruned_at=now,
                last_pruned_count=deleted_count,
            )
            db.add(row)
            await db.flush()
            return
        existing.last_pruned_at = now
        existing.last_pruned_count = deleted_count
        await db.flush()
