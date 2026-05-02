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
- ``webhook_triggers``   — outbound webhooks fired on session lifecycle changes
- ``ingest_tokens``      — per-source webhook credentials for external incident ingestion
- ``ingest_log``         — raw payloads from external ingest for replay/debugging
- ``detector_rules``     — MCP-driven incident detection probes (one per MCP server)
- ``detector_history``   — run history for each detector rule
- ``bot_connectors``     — external chat bot connector configurations
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
    Numeric,
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open"
    )  # open | investigating | resolved | closed
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
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

    sessions: Mapped[list[Session]] = relationship(back_populates="incident")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class Session(Base):
    __tablename__ = "sessions"

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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
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


# ---------------------------------------------------------------------------
# Workflow profiles (custom workflow builder — Phase 3)
# ---------------------------------------------------------------------------


class WorkflowProfile(Base):
    __tablename__ = "workflow_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
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


# ---------------------------------------------------------------------------
# Agent team profiles (multi-agent support — Phase 3)
# ---------------------------------------------------------------------------


class AgentTeamProfile(Base):
    __tablename__ = "agent_team_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # auto | cloudwatch | azure_monitor | gcp_monitoring | oci_monitoring | legacy_alert_vendor | legacy_alert_relay | generic
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    shape_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
        Uuid,
        ForeignKey("detector_rules.id", ondelete="CASCADE"),
        nullable=False,
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


# ---------------------------------------------------------------------------
# SLA Targets (Sprint 25)
# ---------------------------------------------------------------------------


class SLATarget(Base):
    """Reliability tracking target (HTTP, TCP, or externally ingested)."""

    __tablename__ = "sla_targets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
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


# ---------------------------------------------------------------------------
# Uptime Samples (Sprint 25)
# ---------------------------------------------------------------------------


class UptimeSample(Base):
    """Raw availability probes for SLA targets."""

    __tablename__ = "uptime_samples"

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

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sla_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    objective_pct: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False
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
# Maintenance Windows (Sprint 25)
# ---------------------------------------------------------------------------


class MaintenanceWindow(Base):
    """Scheduled downtime suppressing SLA hits."""

    __tablename__ = "maintenance_windows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False
    )  # UUIDs as strings
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Bot connectors (Sprint 27)
# ---------------------------------------------------------------------------


class BotConnector(Base):
    """External chat bot connector configuration."""

    __tablename__ = "bot_connectors"

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
    """Map an external chat platform user to an AIM user account."""

    __tablename__ = "bot_user_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("bot_connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    aim_user_id: Mapped[uuid.UUID] = mapped_column(
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
