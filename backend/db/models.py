"""SQLAlchemy ORM models for AI Incident Manager.

Maps the data model from REFERENCE.md to Postgres tables:
- ``users``              — auth users with roles
- ``incidents``          — top-level incident records
- ``sessions``           — incident response sessions (one per ``aim run``)
- ``audit_entries``      — every agent action (replaces JSONL backend)
- ``approval_requests``  — Tier 1 human-approval queue
- ``model_configs``      — BYOM provider configurations
- ``mcp_servers``        — persisted MCP connection definitions
- ``runtime_config``     — DB-backed UI overrides for runtime settings
- ``skills``             — operator-owned skill definitions (optionally bound to an MCP server)
- ``session_messages``   — co-pilot chat history (user ↔ assistant), parallel to the workflow
- ``ingest_tokens``      — per-source webhook credentials for external incident ingestion
- ``ingest_log``         — raw payloads from external ingest for replay/debugging
- ``detector_rules``     — MCP-driven incident detection probes (one per MCP server)
- ``detector_history``   — run history for each detector rule
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    Uuid,
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
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )  # admin | operator | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )  # open | investigating | resolved | closed
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # External ingestion fingerprint — dedup by (external_source, external_id)
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    sessions: Mapped[list[Session]] = relationship(back_populates="incident")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id"), nullable=True
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
    audit_entries: Mapped[list[AuditEntry]] = relationship(back_populates="session")
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="session"
    )


# ---------------------------------------------------------------------------
# Audit entries
# ---------------------------------------------------------------------------

class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
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

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # anthropic | openai | azure_openai | ollama
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_env_var: Mapped[str | None] = mapped_column(String(100), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    temperature: Mapped[float] = mapped_column(default=0.0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_vars: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
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


# ---------------------------------------------------------------------------
# Session messages (co-pilot chat)
# ---------------------------------------------------------------------------

class SessionMessage(Base):
    __tablename__ = "session_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # user | assistant
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

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Ingest tokens (external incident ingestion — Sprint 14)
# ---------------------------------------------------------------------------

class IngestToken(Base):
    """Per-source credentials for the ``POST /incidents/ingest`` webhook.

    The raw token is returned **only** on creation.  Subsequent reads
    expose ``token_hash`` metadata but never the secret.
    """
    __tablename__ = "ingest_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # cloudwatch | azure_monitor | legacy_alert_vendor | generic
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Ingest log (raw payload audit trail)
# ---------------------------------------------------------------------------

class IngestLog(Base):
    """Every inbound webhook payload stored raw for replay/debugging."""
    __tablename__ = "ingest_log"

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
# Detector rules (MCP-driven incident detection — Sprint 14)
# ---------------------------------------------------------------------------

class DetectorRule(Base):
    """One detection probe against one MCP server.

    Periodically runs a read-only LLM loop to inspect the MCP server
    and auto-files an incident when something looks wrong.
    """
    __tablename__ = "detector_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    interval_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False
    )  # default: every 5 minutes
    severity_default: Mapped[str] = mapped_column(
        String(20), default="medium", nullable=False
    )  # critical | high | medium | low
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ran_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_fingerprint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Detector history (run log for detector rules)
# ---------------------------------------------------------------------------

class DetectorHistory(Base):
    """One row per detector rule execution."""
    __tablename__ = "detector_history"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("detector_rules.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )
    raw_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

