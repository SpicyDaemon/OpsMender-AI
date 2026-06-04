"""SQLAlchemy ORM models for OpsMender AI.

Maps the data model from REFERENCE.md to Postgres tables:
- ``users``              — auth users with roles
- ``incidents``          — top-level incident records
- ``sessions``           — incident response sessions (one per ``opsmender run``)
- ``audit_entries``      — every agent action (replaces JSONL backend)
- ``approval_requests``  — Tier 1 human-approval queue
- ``model_configs``      — BYOM provider configurations
- ``mcp_servers``        — persisted MCP connection definitions
- ``runtime_config``     — DB-backed UI overrides for runtime settings
- ``skills``             — operator-owned skill definitions (optionally bound to an MCP server)
- ``session_messages``   — co-pilot chat history (user ↔ assistant), parallel to the workflow
- ``webhook_triggers``   — outbound webhooks fired on session lifecycle changes
- ``ingest_tokens``      — per-source webhook credentials for external incident ingestion
- ``ingest_log``         — raw payloads from external ingest for replay/debugging
- ``bot_connectors``     — external chat bot connector configurations
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared base for all ORM models."""

    pass


# ---------------------------------------------------------------------------
# Organizations (Phase 4)
# ---------------------------------------------------------------------------


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    branding: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority_llm_escalation_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    notification_dedup_window_minutes: Mapped[int] = mapped_column(
        Integer, default=10, nullable=False
    )
    slack_incident_channels_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    users: Mapped[list["UserOrganization"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    domains: Mapped[list["OrganizationDomain"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationDomain(Base):
    """Host-based tenant routing — maps a hostname to an organization.

    Multiple domains may point at one org (e.g. ``acme.opsmender.example.com`` plus a
    custom CNAME ``incidents.acme.com``). One row per org is flagged primary
    so the UI can render canonical links.
    """

    __tablename__ = "organization_domains"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="domains")


class OrgSSOConfig(Base):
    """Per-organization SSO / OIDC configuration.

    One row per org (UNIQUE org_id). Drives the ``/auth/sso/{slug}/login``
    flow: OpsMender redirects the user to the IdP described here, validates the
    returned id_token, and JIT-provisions the user into the org.
    """

    __tablename__ = "org_sso_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    provider: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'oidc' (saml deferred)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    discovery_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(
        String(255), nullable=False, default="openid email profile"
    )
    email_claim: Mapped[str] = mapped_column(
        String(64), nullable=False, default="email"
    )
    name_claim: Mapped[str] = mapped_column(String(64), nullable=False, default="name")
    default_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )
    allowed_email_domains: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class OrgSAMLConfig(Base):
    """Per-organization SAML 2.0 SSO configuration (Sprint 30).

    Sibling to :class:`OrgSSOConfig` — the columns don't meaningfully overlap
    so each protocol gets its own table to keep NOT NULL constraints honest.

    The IdP is described by either a metadata URL (preferred, auto-fetched +
    cached for 10 minutes) or by raw XML pasted by the admin. Exactly one of
    those two columns is set at any given time (enforced at the API layer).

    SP-side keypair lives in env (``OPSMENDER_SAML_SP_CERT`` / ``OPSMENDER_SAML_SP_KEY``)
    and is shared across all tenants — see :class:`SAMLConfig` in
    ``backend/config_loader.py``.
    """

    __tablename__ = "org_saml_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    idp_metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    idp_metadata_xml: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_attribute: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    )
    name_attribute: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    )
    default_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )
    allowed_email_domains: Mapped[str | None] = mapped_column(Text, nullable=True)
    want_assertions_signed: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    want_response_signed: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class UserOrganization(Base):
    __tablename__ = "user_organizations"
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )  # admin | operator | viewer
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="organizations")
    organization: Mapped[Organization] = relationship(back_populates="users")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    auth_source: Mapped[str] = mapped_column(
        String(255), nullable=False, default="local"
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )  # admin | operator | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Profile fields (optional). avatar_color is a palette key driving the
    # generated initials avatar — no file storage in v1.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    primary_org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    # Sprint 56: soft-delete marker. When set, the row stays for FK reference
    # but the user is hidden from active lists and cannot log in. Username is
    # preserved for historical display; email + password_hash are scrubbed.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organizations: Mapped[list["UserOrganization"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OrgInvite(Base):
    """Sprint 56 — admin-initiated invite to join an organization.

    Issued by an admin via ``POST /organizations/{id}/invites``. The
    one-time URL returned to the admin embeds the raw token; only the
    sha256 hash is persisted. Accepting the invite mints the user and
    binds them to the org with the role recorded here.
    """

    __tablename__ = "org_invites"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin | operator | viewer
    # Optional names supplied by the admin; prefill the accept page.
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class PasswordResetToken(Base):
    """Sprint 56 — admin-minted one-time password reset token.

    Issued by an admin via ``POST /auth/users/{id}/reset-password``. Only
    the sha256 hash is persisted; the raw token is returned exactly once
    in the response (and optionally emailed best-effort via SMTP).
    """

    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class Incident(Base):
    __tablename__ = "incidents"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )  # open | investigating | resolved | closed
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Paging surface (Sprint 33). Priority + response mode set at creation
    # time and locked thereafter; service_id ties the incident to its owning
    # service for routing decisions.
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)
    response_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    suppressed_by_maintenance_window_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("maintenance_windows.id", ondelete="SET NULL"), nullable=True
    )
    # Sprint 36 step 5 — per-incident Slack channel mirror. Populated when
    # the chain starts in ``page`` mode AND the org opt-in is on.
    slack_channel_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slack_channel_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # External ingestion fingerprint — dedup by (external_source, external_id)
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sla_targets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # Sprint 61 Step 4 — operator-authored postmortem markdown plus its
    # own edit timestamp so the UI can show "last edited" without
    # conflating with the incident's own lifecycle clock.
    postmortem_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    postmortem_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list[Session]] = relationship(back_populates="incident")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id"), nullable=True
    )
    workflow_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_profiles.id", ondelete="SET NULL"), nullable=True
    )
    agent_team_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_team_profiles.id", ondelete="SET NULL"), nullable=True
    )
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # active | awaiting_approval | completed | failed | timed_out
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    incident: Mapped[Incident | None] = relationship(back_populates="sessions")
    workflow_profile: Mapped["WorkflowProfile | None"] = relationship()
    agent_team_profile: Mapped["AgentTeamProfile | None"] = relationship()
    audit_entries: Mapped[list[AuditEntry]] = relationship(back_populates="session")
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="session"
    )


# ---------------------------------------------------------------------------
# Audit entries
# ---------------------------------------------------------------------------


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # tool_call_start | tool_call_end | tool_call_blocked | session_start | session_end
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    permitted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped[Session] = relationship(back_populates="audit_entries")


# ---------------------------------------------------------------------------
# Approval requests (Tier 1)
# ---------------------------------------------------------------------------


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id"), nullable=False
    )
    action: Mapped[dict] = mapped_column(JSON, nullable=False)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | approved | rejected | expired
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    session: Mapped[Session] = relationship(back_populates="approval_requests")
    resolver: Mapped[User | None] = relationship()


# ---------------------------------------------------------------------------
# Model configs (BYOM)
# ---------------------------------------------------------------------------


class ModelConfig(Base):
    __tablename__ = "model_configs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # anthropic | openai | azure_openai | ollama
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_env_var: Mapped[str | None] = mapped_column(String(100), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    temperature: Mapped[float] = mapped_column(default=0.0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_model_config_name"),)


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_vars: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_successful_call_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_mcp_server_name"),)


class MCPServerOAuthToken(Base):
    """OAuth 2.1 token bundle for an HTTP-transport MCP server (Sprint 42).

    One row per ``(org_id, mcp_server_id)``. ``access_token`` and
    ``refresh_token`` are encrypted at rest with the project-wide Fernet
    helper in ``backend/auth/secrets.py`` — repo callers pass plaintext;
    encryption happens at the boundary.

    The ``issuer`` column captures the authorization-server issuer
    recorded at authorize-request time, used for RFC 9207 validation
    when redirects come back and on every subsequent refresh.

    The spec says refresh tokens MAY NOT be issued (OAuth 2.1 §4.3 / MCP
    authz §6.4), so ``refresh_token_encrypted`` is nullable. The auto-
    refresh path treats absence as "operator must reauthorize."
    """

    __tablename__ = "mcp_server_oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # exactly one OAuth row per MCP server
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Bearer"
    )
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issuer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OAuth client credentials used for refresh (DCR client_id or pre-registered).
    # client_secret is encrypted; client_id is stored plaintext (it's not sensitive).
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    obtained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Auto-refresh poll uses (org_id, expires_at) to find tokens about
        # to expire across an org's MCP fleet.
        Index(
            "ix_mcp_server_oauth_tokens_org_expires",
            "org_id",
            "expires_at",
        ),
    )


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class Skill(Base):
    __tablename__ = "skills"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("mcp_servers.id", ondelete="SET NULL"), nullable=True
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_skill_name"),)


# ---------------------------------------------------------------------------
# Session messages (co-pilot chat)
# ---------------------------------------------------------------------------


class SessionMessage(Base):
    __tablename__ = "session_messages"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    consumed_by_workflow: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    node_context: Mapped[str | None] = mapped_column(String(50), nullable=True)


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Webhook triggers (outbound session-state notifications)
# ---------------------------------------------------------------------------


class WebhookTrigger(Base):
    __tablename__ = "webhook_triggers"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    format: Mapped[str] = mapped_column(String(20), default="generic", nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_webhook_trigger_name"),
    )


# ---------------------------------------------------------------------------
# Workflow profiles (custom workflow builder — Phase 3)
# ---------------------------------------------------------------------------


class WorkflowProfile(Base):
    __tablename__ = "workflow_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_order: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_workflow_profile_name"),
    )


# ---------------------------------------------------------------------------
# Agent team profiles (multi-agent support — Phase 3)
# ---------------------------------------------------------------------------


class AgentTeamProfile(Base):
    __tablename__ = "agent_team_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_agent_team_profile_name"),
    )


# ---------------------------------------------------------------------------
# Ingest tokens (external incident ingestion — Sprint 14)
# ---------------------------------------------------------------------------


class IngestToken(Base):
    """Per-source credentials for the ``POST /incidents/ingest`` webhook.

    The raw token is returned **only** on creation.  Subsequent reads
    expose ``token_hash`` metadata but never the secret.

    ``shape_cache`` holds learned field-path mappings keyed by a hash of
    the payload's top-level structure. Populated either by LLM fallback
    on first-sighting of a payload shape, or by operator-supplied sample
    payloads during token creation — the Universal adapter uses it to
    skip heuristics on repeat traffic.
    """

    __tablename__ = "ingest_tokens"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # auto | cloudwatch | azure_monitor | gcp_monitoring | oci_monitoring | generic
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    shape_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # When set, every incident created through this token gets ``service_id``
    # pre-filled so the paging engine routes to the owning team automatically
    # (Simplification pass 1, post-Sprint 34).
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_ingest_token_name"),)


# ---------------------------------------------------------------------------
# Ingest log (raw payload audit trail)
# ---------------------------------------------------------------------------


class IngestLog(Base):
    """Every inbound webhook payload stored raw for replay/debugging."""

    __tablename__ = "ingest_log"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    ingest_token_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingest_tokens.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id"), nullable=True
    )
    dedup_action: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # created | updated | skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# SLA Targets (Sprint 25)
# ---------------------------------------------------------------------------


class SLATarget(Base):
    """Reliability tracking target (HTTP, TCP, or externally ingested)."""

    __tablename__ = "sla_targets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # http | tcp | external
    config: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # url, method, expected_status, etc.
    owner_team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_sla_target_name"),)


# ---------------------------------------------------------------------------
# Uptime Samples (Sprint 25)
# ---------------------------------------------------------------------------


class UptimeSample(Base):
    """Raw availability probes for SLA targets."""

    __tablename__ = "uptime_samples"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sla_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    up: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # poller | ingest
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UptimeSample5m(Base):
    """5-minute downsampled availability probes."""

    __tablename__ = "uptime_samples_5m"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sla_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    up_pct: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False
    )
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False)


class UptimeSample1h(Base):
    """1-hour downsampled availability probes."""

    __tablename__ = "uptime_samples_1h"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sla_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    up_pct: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False
    )
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False)


# ---------------------------------------------------------------------------
# SLOs (Sprint 25)
# ---------------------------------------------------------------------------


class SLO(Base):
    """Service Level Objectives linked to an SLA Target."""

    __tablename__ = "slos"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sla_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Percentage 0–100 with up to 3 decimal places (e.g. 99.999). Numeric(6,3)
    # holds 100.000 and 99.999; the old Numeric(5,4) capped at 9.9999 and would
    # overflow on Postgres for any realistic SLA objective.
    objective_pct: Mapped[float] = mapped_column(
        Numeric(6, 3, asdecimal=False), nullable=False
    )
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    burn_alert_threshold: Mapped[float | None] = mapped_column(
        Numeric(10, 4, asdecimal=False), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Maintenance Windows (Sprint 25 + Sprint 35)
# ---------------------------------------------------------------------------


class MaintenanceWindow(Base):
    """Scheduled downtime suppressing SLA hits and paging fan-out."""

    __tablename__ = "maintenance_windows"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )  # UUIDs as strings
    scope_type: Mapped[str] = mapped_column(
        String(20), default="global", nullable=False
    )  # global | service | roster | team
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_maintenance_windows_org_range", "org_id", "starts_at", "ends_at"),
        Index("ix_maintenance_windows_scope", "org_id", "scope_type", "scope_id"),
    )

    @property
    def scope_ids(self) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = []
        if self.scope_id is not None:
            ids.append(self.scope_id)
        for raw in self.target_ids or []:
            try:
                value = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
            if value not in ids:
                ids.append(value)
        return ids


class UserNotificationPref(Base):
    """Per-user, per-org paging delivery preferences."""

    __tablename__ = "user_notification_prefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    channels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    routing: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quiet_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_user_notification_pref"),
        Index("ix_user_notification_prefs_org_user", "org_id", "user_id"),
    )


# ---------------------------------------------------------------------------
# Bot connectors (Sprint 27)
# ---------------------------------------------------------------------------


class BotConnector(Base):
    """External chat bot connector configuration."""

    __tablename__ = "bot_connectors"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # telegram | signal | whatsapp | custom
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    credentials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    allowed_capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="not_configured", nullable=False
    )  # not_configured | configured | healthy | error | disabled
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BotUserLink(Base):
    """Map an external chat platform user to an OpsMender user account."""

    __tablename__ = "bot_user_links"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("bot_connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    opsmender_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class BotActionAudit(Base):
    """Audit log entry for an inbound bot connector action."""

    __tablename__ = "bot_action_audit"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("bot_connectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    command: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )


# ---------------------------------------------------------------------------
# Auditor (Sprint 32) — read-only environment scans producing findings reports.
# Distinct from incidents: audits run on demand or on schedule, produce a
# triageable list of findings, and never auto-page humans.
# ---------------------------------------------------------------------------


class AuditRun(Base):
    """One audit run — a set of analyzers executed against an environment."""

    __tablename__ = "audit_runs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )  # queued | running | completed | failed
    analyzers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finding_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    findings: Mapped[list["AuditFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AuditSchedule(Base):
    """Sprint 39 step 2 — scheduled audit runs.

    Background scheduler polls this table; rows with ``is_active`` and a
    past ``next_run_at`` get a new ``audit_runs`` row queued, then their
    ``last_run_at`` / ``next_run_at`` advance by ``interval_minutes``.
    """

    __tablename__ = "audit_schedules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    analyzers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    mcp_server_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    focus_areas: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class AuditFinding(Base):
    """One finding produced by an analyzer during an audit run."""

    __tablename__ = "audit_findings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("audit_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analyzer: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info"
    )  # critical | high | medium | low | info
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )  # open | remediating | resolved | dismissed
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    run: Mapped[AuditRun] = relationship(back_populates="findings")


# ---------------------------------------------------------------------------
# Paging (Sprint 33) — teams, services, rosters, priority rules, assignments.
# Full data model lives in docs/paging-model.md.
# ---------------------------------------------------------------------------


class Team(Base):
    __tablename__ = "teams"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_team_slug"),)


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)


class Service(Base):
    __tablename__ = "services"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(8), default="P2", nullable=False)
    intake_token: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    preferred_mcp_server_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    external_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_service_slug"),)

    @property
    def intake_url(self) -> str | None:
        if not self.intake_token:
            return None
        return f"/api/v1/intake/{self.intake_token}"


class Roster(Base):
    __tablename__ = "rosters"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    pattern: Mapped[str] = mapped_column(String(20), default="weekly", nullable=False)
    pattern_length: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    coverage_start_time: Mapped[str] = mapped_column(
        String(8), default="09:00", nullable=False
    )
    coverage_end_time: Mapped[str] = mapped_column(
        String(8), default="17:00", nullable=False
    )
    handoff_time: Mapped[str] = mapped_column(
        String(8), default="09:00", nullable=False
    )
    handoff_day: Mapped[str | None] = mapped_column(String(12), nullable=True)
    anchor_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class RosterMember(Base):
    __tablename__ = "roster_members"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    roster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("rosters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    position_index: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("roster_id", "user_id", name="uq_roster_member_user"),
        UniqueConstraint(
            "roster_id", "position_index", name="uq_roster_member_position"
        ),
    )


class RosterOverride(Base):
    __tablename__ = "roster_overrides"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    roster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("rosters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    covering_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class ServiceRoster(Base):
    __tablename__ = "service_rosters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    roster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("rosters.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("service_id", "roster_id", name="uq_service_roster"),
    )


class PriorityRule(Base):
    __tablename__ = "priority_rules"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    response_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (Index("ix_priority_rules_org_index", "org_id", "rule_index"),)


class PriorityLLMOverrideLog(Base):
    __tablename__ = "priority_llm_override_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    rule_priority: Mapped[str] = mapped_column(String(8), nullable=False)
    llm_priority: Mapped[str] = mapped_column(String(8), nullable=False)
    llm_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class IncidentAssignment(Base):
    __tablename__ = "incident_assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assigned_to: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by: Mapped[str] = mapped_column(String(30), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_incident_assignments_active",
            "incident_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
            sqlite_where=text("released_at IS NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# Escalation chains (Sprint 34) — additive paging engine. State machine lives
# in backend/paging/escalation.py; the tables here only persist the chain
# definition + per-incident page log.
# ---------------------------------------------------------------------------


class EscalationChain(Base):
    __tablename__ = "escalation_chains"

    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class EscalationStep(Base):
    __tablename__ = "escalation_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("escalation_chains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # roster | user | team
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    notify_channels: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("chain_id", "step_index", name="uq_escalation_step_index"),
    )


class ServiceEscalationChain(Base):
    __tablename__ = "service_escalation_chains"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("escalation_chains.id", ondelete="CASCADE"), nullable=False
    )
    applies_when: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("service_id", "chain_id", name="uq_service_escalation_chain"),
    )


class IncidentPage(Base):
    """One row per page attempt — used as the audit log for the chain engine
    in Sprint 34. Sprint 35 will wire ``channel`` to real delivery surfaces."""

    __tablename__ = "incident_pages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    chain_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("escalation_chains.id", ondelete="SET NULL"), nullable=True
    )
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(40), default="recorded", nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    ack_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ack_via: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(20), default="recorded", nullable=False
    )
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Per-incident chain run state (Sprint 34) — tracks current step, next
# deadline, soft-takeover window, etc. Separate from the chain *definition*.
# ---------------------------------------------------------------------------


class IncidentChainState(Base):
    __tablename__ = "incident_chain_states"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("escalation_chains.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False
    )  # running | paused | acked | exhausted | resolved | cancelled
    current_step_index: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    next_step_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hard_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pending_takeover_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    pending_takeover_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationEscalation(Base):
    """Per-(incident, user) staged notification escalation.

    Drives the priority's ordered routing stages: stage 0 fires immediately,
    each later stage fires after the prior stage's ``delay_seconds`` if the
    incident is still unacknowledged. Acknowledgement or resolution stops it.
    Independent of the chain engine (which escalates across people).
    """

    __tablename__ = "notification_escalations"
    __table_args__ = (
        UniqueConstraint(
            "incident_id", "user_id", name="uq_notification_escalation_incident_user"
        ),
        Index("ix_notification_escalations_due", "org_id", "status", "next_stage_due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Ordered stages: [{"channel_id": str, "delay_seconds": int}, ...]
    stages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False
    )  # running | acked | resolved | exhausted | cancelled
    current_stage: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    next_stage_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# AI incident memory (Sprint 45 — D-025)
# ---------------------------------------------------------------------------


class IncidentMemory(Base):
    """A short markdown lesson distilled from a successfully resolved incident.

    The agent reads matching memories at session start (advisory context) and
    writes a new one after `summarize` on resolved sessions. Operators can also
    author and edit memories by hand. Memory cannot bypass tier or skill
    enforcement — it is context only.
    """

    __tablename__ = "incident_memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    source_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unhelpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_incident_memories_org_service", "org_id", "service_id"),
        Index("ix_incident_memories_org_hidden", "org_id", "is_hidden"),
    )


class IncidentMemoryRecallLog(Base):
    """One row per (memory, session) surfacing event.

    Used for analytics, audit ("which memories shaped this session?"), and for
    populating the "Memories used" panel on the session detail page.
    """

    __tablename__ = "incident_memory_recall_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("incident_memories.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    surfaced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    score: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)

    __table_args__ = (
        Index(
            "ix_incident_memory_recall_session", "session_id", "surfaced_at"
        ),
    )


# ---------------------------------------------------------------------------
# Data retention (Sprint 53)
# ---------------------------------------------------------------------------


class RetentionConfig(Base):
    """Per-org per-category TTL for high-volume log tables (Sprint 53).

    Categories under retention control:
        - audit_entries
        - ingest_log
        - incident_memory_recall_log
        - bot_action_audit

    ``ttl_days`` semantics:
        - NULL => retention disabled for this category (never auto-delete).
        - integer >= 1 => rows older than (now - ttl_days) are pruned by the
          nightly RetentionScheduler.

    When no row exists for an (org, category) pair, the pruner falls back to
    the system default of 90 days (Sprint 53 D-026).
    """

    __tablename__ = "retention_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_pruned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_pruned_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("org_id", "category", name="uq_retention_org_category"),
    )
