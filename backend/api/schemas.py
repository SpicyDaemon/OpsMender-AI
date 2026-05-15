"""Pydantic request/response schemas for the OpsMender API.

All schemas live in one file to avoid circular imports and make it easy
to see the full API surface at a glance.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    email: str = Field(..., max_length=255)  # EmailStr needs email-validator
    password: str = Field(..., min_length=8)
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: str
    is_active: bool
    primary_org_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Optional[str] = Field(
        default=None, pattern="^(critical|high|medium|low)$"
    )


class IncidentResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    severity: Optional[str]
    external_id: Optional[str] = None
    external_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    incident_id: Optional[uuid.UUID] = None
    workflow_profile_id: Optional[uuid.UUID] = None
    agent_team_profile_id: Optional[uuid.UUID] = None
    tier: int = Field(..., ge=0, le=3)
    model_provider: Optional[str] = None
    model_id: Optional[str] = None
    initial_briefing: Optional[str] = Field(default=None, max_length=10000)


class SessionResponse(BaseModel):
    id: uuid.UUID
    incident_id: Optional[uuid.UUID]
    workflow_profile_id: Optional[uuid.UUID]
    agent_team_profile_id: Optional[uuid.UUID]
    tier: int
    model_provider: Optional[str]
    model_id: Optional[str]
    status: str
    summary: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    tier0_max_session_seconds: Optional[int] = None

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


# -- Rollback (Sprint 17) ----------------------------------------------------


class SessionRollbackRequest(BaseModel):
    """Trigger rollback of a session's executed operations.

    ``mcp_server`` names the MCP server to connect to for issuing the
    compensating-inverse calls.  When ``dry_run=true`` the endpoint
    returns the rollback plan without actually invoking any inverse.
    """

    mcp_server: Optional[str] = Field(
        default=None,
        description="MCP server name. Required unless dry_run is true.",
    )
    dry_run: bool = False


class RollbackStepResponse(BaseModel):
    original_tool: str
    inverse_tool: Optional[str]
    parameters: dict[str, Any]
    status: str  # succeeded | failed | skipped_no_inverse | skipped_not_permitted
    error: Optional[str] = None


class SessionRollbackResponse(BaseModel):
    session_id: uuid.UUID
    dry_run: bool
    attempted: int
    succeeded: int
    failed: int
    skipped: int
    steps: list[RollbackStepResponse]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEntryResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    timestamp: datetime
    tier: int
    entry_type: str
    tool_name: Optional[str]
    tool_parameters: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    permitted: bool
    block_reason: Optional[str]
    duration_ms: Optional[int]

    model_config = {"from_attributes": True}


class AuditListResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalRequestResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    action: dict[str, Any]
    justification: Optional[str]
    status: str
    requested_at: datetime
    resolved_at: Optional[datetime]
    resolved_by: Optional[uuid.UUID]
    expires_at: datetime

    model_config = {"from_attributes": True}


class ApprovalListResponse(BaseModel):
    items: list[ApprovalRequestResponse]
    total: int


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    tier: int
    mcp_servers: list[dict[str, Any]]
    audit_output: str
    logging_level: str
    ingest_auto_start_enabled: bool
    ingest_auto_start_min_severity: str
    ingest_auto_start_source: Optional[str] = None


class ConfigUpdate(BaseModel):
    tier: Optional[int] = Field(default=None, ge=0, le=3)
    logging_level: Optional[str] = Field(
        default=None, pattern="^(DEBUG|INFO|WARNING|ERROR)$"
    )
    ingest_auto_start_enabled: Optional[bool] = None
    ingest_auto_start_min_severity: Optional[str] = Field(
        default=None,
        pattern="^(critical|high|medium|low)$",
    )
    ingest_auto_start_source: Optional[str] = Field(default=None, max_length=100)


class ModelConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model_id: str
    api_key_env_var: Optional[str]
    base_url: Optional[str]
    api_version: Optional[str]
    max_tokens: int
    temperature: float
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelConfigListResponse(BaseModel):
    items: list[ModelConfigResponse]
    total: int


class ModelConfigValidationIssue(BaseModel):
    code: str
    message: str


class ModelConfigSaveResponse(BaseModel):
    config: ModelConfigResponse
    warnings: list[ModelConfigValidationIssue] = Field(default_factory=list)


class ModelBootstrapStatusResponse(BaseModel):
    needs_setup: bool
    has_configs: bool
    has_default: bool
    default_config: Optional[ModelConfigResponse] = None


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: str = Field(pattern="^(anthropic|openai|azure_openai|ollama)$")
    model_id: str = Field(..., min_length=1, max_length=200)
    api_key_env_var: Optional[str] = Field(default=None, max_length=100)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_version: Optional[str] = Field(default=None, max_length=50)
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: Optional[str]
    args: Optional[list[str]]
    url: Optional[str]
    env_vars: Optional[dict[str, str]]
    is_active: bool
    created_at: datetime
    has_token: bool


class MCPServerListResponse(BaseModel):
    items: list[MCPServerResponse]
    total: int


class MCPServerUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(pattern="^(stdio|sse|http)$")
    command: Optional[str] = Field(default=None, max_length=500)
    args: Optional[list[str]] = None
    url: Optional[str] = Field(default=None, max_length=1000)
    token: Optional[str] = None
    clear_token: bool = False
    env_vars: Optional[dict[str, str]] = None
    is_active: bool = True


class MCPServerTestResponse(BaseModel):
    success: bool
    detail: str
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    mcp_server_id: Optional[uuid.UUID]
    content_md: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillListResponse(BaseModel):
    items: list[SkillResponse]
    total: int


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    content_md: str = Field(..., min_length=1)
    description: Optional[str] = None
    mcp_server_id: Optional[uuid.UUID] = None


class SkillUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    content_md: str = Field(..., min_length=1)
    description: Optional[str] = None
    mcp_server_id: Optional[uuid.UUID] = None


class SkillCloneRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    mcp_server_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class ProviderModelsResponse(BaseModel):
    provider: str
    label: str
    default_model_id: str
    default_api_key_env_var: Optional[str]
    requires_api_key: bool
    requires_base_url: bool
    requires_api_version: bool
    available: bool
    models: list[str]
    error: Optional[str]


class ProviderModelsListResponse(BaseModel):
    items: list[ProviderModelsResponse]
    total: int


# ---------------------------------------------------------------------------
# Session messages (co-pilot chat)
# ---------------------------------------------------------------------------


class SessionMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class SessionMessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str  # user | assistant
    content: str
    created_at: datetime
    consumed_by_workflow: bool
    node_context: Optional[str]

    model_config = {"from_attributes": True}


class SessionMessageListResponse(BaseModel):
    items: list[SessionMessageResponse]
    total: int


# ---------------------------------------------------------------------------
# Webhook triggers (outbound session-state notifications)
# ---------------------------------------------------------------------------


class WebhookTriggerUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    url: str = Field(..., min_length=1, max_length=1000)
    format: str = Field(default="generic", pattern="^(generic|slack|teams|sumo)$")
    event_types: list[str] = Field(..., min_length=1)
    headers: Optional[dict[str, str]] = None
    clear_headers: bool = False
    token: Optional[str] = None
    clear_token: bool = False
    is_active: bool = True


class WebhookTriggerResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    format: str
    event_types: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_triggered_at: Optional[datetime]
    last_error: Optional[str]
    header_names: list[str] = Field(default_factory=list)
    has_token: bool

    model_config = {"from_attributes": True}


class WebhookTriggerListResponse(BaseModel):
    items: list[WebhookTriggerResponse]
    total: int


class WebhookTriggerTestResponse(BaseModel):
    success: bool
    detail: str
    status_code: Optional[int] = None
    event_type: str


# ---------------------------------------------------------------------------
# Workflow profiles (custom workflow builder — Phase 3)
# ---------------------------------------------------------------------------


class WorkflowProfileUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    node_order: list[str] = Field(..., min_length=1)
    is_active: bool = True
    is_default: bool = False


class WorkflowProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    node_order: list[str]
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowProfileListResponse(BaseModel):
    items: list[WorkflowProfileResponse]
    total: int


# ---------------------------------------------------------------------------
# Agent team profiles (multi-agent support — Phase 3)
# ---------------------------------------------------------------------------


class AgentTeamProfileUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    roles: list[str] = Field(..., min_length=1)
    is_active: bool = True
    is_default: bool = False


class AgentTeamProfileResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    roles: list[str]
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentTeamProfileListResponse(BaseModel):
    items: list[AgentTeamProfileResponse]
    total: int


# ---------------------------------------------------------------------------
# Ingest tokens (Sprint 14)
# ---------------------------------------------------------------------------


class IngestTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    provider: str = Field(
        default="auto",
        pattern="^(auto|cloudwatch|azure_monitor|gcp_monitoring|oci_monitoring|legacy_alert_vendor|legacy_alert_relay|generic)$",
    )
    # Optional sample payload from the source tool — if supplied, the
    # server parses it on create so future payloads with the same shape
    # skip the LLM fallback.
    sample_payload: Optional[dict] = None


class IngestTokenResponse(BaseModel):
    """Returned on list/get — never exposes the raw token."""

    id: uuid.UUID
    name: str
    provider: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    shape_cache_size: int = 0  # number of learned payload shapes

    model_config = {"from_attributes": True}


class IngestTokenCreatedResponse(BaseModel):
    """Returned only on creation — includes the raw token once."""

    id: uuid.UUID
    name: str
    provider: str
    token: str  # raw token — shown once, never stored
    is_active: bool
    created_at: datetime


class IngestTokenLearnShapeRequest(BaseModel):
    payload: dict


class IngestLearnPreview(BaseModel):
    title: str
    description: str
    severity: Optional[str] = None
    external_id: Optional[str] = None
    status: str


class IngestTokenLearnShapeResponse(BaseModel):
    """Result of training a token on a sample payload."""

    shape_hash: str
    paths: dict[str, str]
    cache_hit: bool
    preview: IngestLearnPreview


class IngestTokenListResponse(BaseModel):
    items: list[IngestTokenResponse]
    total: int


class IngestResponse(BaseModel):
    """Response from POST /incidents/ingest webhook."""

    success: bool
    incident_id: Optional[uuid.UUID] = None
    dedup_action: Optional[str] = None  # created | updated | skipped
    error: Optional[str] = None


class IngestProviderResponse(BaseModel):
    key: str
    label: str


class IngestProviderListResponse(BaseModel):
    items: list[IngestProviderResponse]


# ---------------------------------------------------------------------------
# Detector rules (MCP-driven incident detection)
# ---------------------------------------------------------------------------


class DetectorRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    mcp_server_id: uuid.UUID
    prompt_template: str = Field(..., min_length=1)
    model_config_id: Optional[uuid.UUID] = None
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    severity_default: str = Field(
        default="medium", pattern="^(critical|high|medium|low)$"
    )
    is_active: bool = True


class DetectorRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    mcp_server_id: Optional[uuid.UUID] = None
    prompt_template: Optional[str] = Field(None, min_length=1)
    model_config_id: Optional[uuid.UUID] = None
    interval_seconds: Optional[int] = Field(None, ge=30, le=86400)
    severity_default: Optional[str] = Field(
        None, pattern="^(critical|high|medium|low)$"
    )
    is_active: Optional[bool] = None


class DetectorRuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    mcp_server_id: uuid.UUID
    prompt_template: str
    model_config_id: Optional[uuid.UUID]
    interval_seconds: int
    severity_default: str
    is_active: bool
    last_ran_at: Optional[datetime]
    last_fingerprint: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DetectorRuleListResponse(BaseModel):
    items: list[DetectorRuleResponse]
    total: int


class DetectorHistoryResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    ran_at: datetime
    duration_ms: Optional[int]
    issue_detected: bool
    incident_id: Optional[uuid.UUID]
    raw_verdict: Optional[dict] = None
    error: Optional[str]

    model_config = {"from_attributes": True}


class DetectorHistoryListResponse(BaseModel):
    items: list[DetectorHistoryResponse]
    total: int


class DetectorRunResponse(BaseModel):
    """Response from POST /detectors/{id}/run (on-demand execution)."""

    success: bool
    issue_detected: bool = False
    incident_id: Optional[uuid.UUID] = None
    error: Optional[str] = None


class DetectorTemplateResponse(BaseModel):
    key: str
    label: str
    description: str
    prompt_template: str
    severity_default: str
    interval_seconds: int


class DetectorTemplateListResponse(BaseModel):
    items: list[DetectorTemplateResponse]
    total: int


# ---------------------------------------------------------------------------
# SLA Targets (Sprint 25)
# ---------------------------------------------------------------------------


class SLATargetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., pattern="^(http|tcp|external)$")
    config: Optional[dict[str, Any]] = None
    owner_team: Optional[str] = Field(default=None, max_length=100)
    is_active: bool = True


class SLATargetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[str] = Field(None, pattern="^(http|tcp|external)$")
    config: Optional[dict[str, Any]] = None
    owner_team: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None


class SLATargetResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    config: Optional[dict[str, Any]]
    owner_team: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SLATargetListResponse(BaseModel):
    items: list[SLATargetResponse]
    total: int


# ---------------------------------------------------------------------------
# SLOs (Sprint 25)
# ---------------------------------------------------------------------------


class SLOCreate(BaseModel):
    target_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    objective_pct: float = Field(..., ge=0.0, le=100.0)
    window_seconds: int = Field(..., ge=3600)  # min 1 hour
    burn_alert_threshold: Optional[float] = Field(default=None, ge=0.0)
    is_active: bool = True


class SLOUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    objective_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    window_seconds: Optional[int] = Field(None, ge=3600)
    burn_alert_threshold: Optional[float] = None
    is_active: Optional[bool] = None


class SLOResponse(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    name: str
    objective_pct: float
    window_seconds: int
    burn_alert_threshold: Optional[float]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SLOListResponse(BaseModel):
    items: list[SLOResponse]
    total: int


class SLOStatusResponse(BaseModel):
    """Computed SLO compliance status."""

    slo_id: uuid.UUID
    target_id: uuid.UUID
    name: str
    objective_pct: float
    actual_pct: float
    error_budget_remaining_pct: float
    burn_rate: float
    compliant: bool


# ---------------------------------------------------------------------------
# Maintenance Windows (Sprint 25)
# ---------------------------------------------------------------------------


class MaintenanceWindowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    reason: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    rrule: Optional[str] = None
    target_ids: list[str] = Field(..., min_length=1)


class MaintenanceWindowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    reason: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    rrule: Optional[str] = None
    target_ids: Optional[list[str]] = None


class MaintenanceWindowResponse(BaseModel):
    id: uuid.UUID
    name: str
    reason: Optional[str]
    starts_at: datetime
    ends_at: datetime
    rrule: Optional[str]
    target_ids: list[str]
    created_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceWindowListResponse(BaseModel):
    items: list[MaintenanceWindowResponse]
    total: int


# ---------------------------------------------------------------------------
# Uptime queries (Sprint 25)
# ---------------------------------------------------------------------------


class UptimeSeriesPoint(BaseModel):
    ts: datetime
    up_pct: float


class UptimeResponse(BaseModel):
    """Aggregated uptime statistics for a target over a window."""

    target_id: uuid.UUID
    uptime_pct: float
    total_samples: int
    up_samples: int
    downtime_seconds: int
    suppressed_seconds: int
    series: list[UptimeSeriesPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bot connectors (Sprint 27)
# ---------------------------------------------------------------------------


class BotConnectorUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    platform: str = Field(pattern="^(telegram|signal|whatsapp|slack|discord|teams|mattermost|matrix|feishu|dingtalk|wecom|weixin|twilio|email|homeassistant|bluebubbles|custom)$")
    config: Optional[dict] = None
    credentials: Optional[dict] = None
    clear_credentials: bool = False
    allowed_capabilities: list[str] = Field(..., min_length=1)
    status: str = Field(
        default="not_configured",
        pattern="^(not_configured|configured|healthy|error|disabled)$",
    )
    is_enabled: bool = False


class BotConnectorResponse(BaseModel):
    id: uuid.UUID
    name: str
    platform: str
    config: Optional[dict]
    allowed_capabilities: list[str]
    status: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime]
    last_error: Optional[str]
    credential_keys: list[str] = Field(default_factory=list)
    has_credentials: bool

    model_config = {"from_attributes": True}


class BotConnectorListResponse(BaseModel):
    items: list[BotConnectorResponse]
    total: int


class BotConnectorTestResponse(BaseModel):
    success: bool
    detail: str
    status: str


class BotConnectorFieldOption(BaseModel):
    value: str
    label: str


class BotConnectorFieldSchema(BaseModel):
    name: str
    label: str
    kind: str
    group: str
    required: bool
    default: Optional[Any] = None
    helper: Optional[str] = None
    doc_url: Optional[str] = None
    placeholder: Optional[str] = None
    options: list[BotConnectorFieldOption] = Field(default_factory=list)


class BotConnectorPlatformSchema(BaseModel):
    platform: str
    fields: list[BotConnectorFieldSchema]
    oauth_enabled: bool = False


class BotConnectorPlatformListResponse(BaseModel):
    items: list[BotConnectorPlatformSchema]
    total: int


class BotUserLinkCreate(BaseModel):
    platform_user_id: str
    opsmender_user_id: uuid.UUID


class BotUserLinkResponse(BaseModel):
    id: uuid.UUID
    connector_id: uuid.UUID
    platform_user_id: str
    opsmender_user_id: uuid.UUID
    opsmender_username: str
    opsmender_role: str
    created_at: datetime


class BotUserLinkListResponse(BaseModel):
    items: list[BotUserLinkResponse]
    total: int


# ---------------------------------------------------------------------------
# Organizations (Phase 4)
# ---------------------------------------------------------------------------


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=100)
    branding: Optional[dict] = None


class OrganizationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    slug: Optional[str] = Field(None, min_length=1, max_length=100)
    branding: Optional[dict] = None


class OrganizationResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    branding: Optional[dict]
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationListResponse(BaseModel):
    items: list[OrganizationResponse]
    total: int


class UserOrganizationLink(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")


class UserOrganizationResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    email: str
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class OrganizationUserListResponse(BaseModel):
    items: list[UserOrganizationResponse]
    total: int


class MyOrganizationResponse(BaseModel):
    """An organization the current user belongs to."""

    id: uuid.UUID
    name: str
    slug: str
    branding: Optional[dict] = None
    role: str
    is_primary: bool

    model_config = {"from_attributes": True}


class MyOrganizationListResponse(BaseModel):
    items: list[MyOrganizationResponse]
    total: int


class OrganizationDomainCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255)
    is_primary: bool = False
    verified: bool = True


class OrganizationDomainResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    domain: str
    is_primary: bool
    verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationDomainListResponse(BaseModel):
    items: list[OrganizationDomainResponse]
    total: int


class TenantContextResponse(BaseModel):
    """Returned by /tenant/resolve so the frontend can show whether the
    current Host pins a tenant."""

    pinned: bool
    org_id: Optional[uuid.UUID] = None
    org_name: Optional[str] = None
    org_slug: Optional[str] = None
    branding: Optional[dict] = None
    host: Optional[str] = None
    sso_enabled: bool = False
    sso_login_path: Optional[str] = None  # e.g. /auth/sso/{slug}/login
    saml_enabled: bool = False
    saml_login_path: Optional[str] = None  # e.g. /auth/saml/{slug}/login


class OrgSSOConfigCreate(BaseModel):
    provider: str = Field(default="oidc", pattern="^(oidc)$")
    discovery_url: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    client_secret: Optional[str] = Field(
        default=None,
        description="Plaintext on input. Omit on update to keep the existing secret.",
    )
    is_active: bool = True
    scopes: str = "openid email profile"
    email_claim: str = "email"
    name_claim: str = "name"
    default_role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")
    allowed_email_domains: Optional[str] = None  # comma-separated


class OrgSSOConfigResponse(BaseModel):
    """SSO config response — never returns the encrypted client secret."""

    id: uuid.UUID
    org_id: uuid.UUID
    provider: str
    is_active: bool
    discovery_url: str
    client_id: str
    has_client_secret: bool
    scopes: str
    email_claim: str
    name_claim: str
    default_role: str
    allowed_email_domains: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrgSAMLConfigCreate(BaseModel):
    """Create / update payload for per-tenant SAML SSO (Sprint 30).

    Provide exactly one of ``idp_metadata_url`` (preferred — auto-fetched and
    cached) or ``idp_metadata_xml`` (raw XML pasted into the form).
    """

    is_active: bool = True
    idp_metadata_url: Optional[str] = None
    idp_metadata_xml: Optional[str] = None
    email_attribute: str = (
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
    )
    name_attribute: str = (
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
    )
    default_role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")
    allowed_email_domains: Optional[str] = None  # comma-separated
    want_assertions_signed: bool = True
    want_response_signed: bool = True


class OrgSAMLConfigResponse(BaseModel):
    """Per-tenant SAML config response."""

    id: uuid.UUID
    org_id: uuid.UUID
    is_active: bool
    idp_metadata_url: Optional[str]
    has_idp_metadata_xml: bool
    email_attribute: str
    name_attribute: str
    default_role: str
    allowed_email_domains: Optional[str]
    want_assertions_signed: bool
    want_response_signed: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------


class WSMessage(BaseModel):
    """Outbound WebSocket message."""

    type: str  # node_transition | tool_call | approval_requested | approval_resolved | chat_message_user | chat_message_assistant | error | session_end
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Auditor (Sprint 32)
# ---------------------------------------------------------------------------


class AuditAnalyzerResponse(BaseModel):
    key: str
    label: str
    description: str


class AuditAnalyzerListResponse(BaseModel):
    items: list[AuditAnalyzerResponse]
    total: int


class AuditRunCreate(BaseModel):
    analyzers: list[str] = Field(..., min_length=1)
    analyzer_params: Optional[dict[str, dict[str, Any]]] = None
    execute: bool = True


class AuditRunResponse(BaseModel):
    id: uuid.UUID
    status: str
    analyzers: list[str] = Field(default_factory=list)
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    finding_count: int
    error: Optional[str] = None
    created_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditRunListResponse(BaseModel):
    items: list[AuditRunResponse]
    total: int


class AuditFindingResponse(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    analyzer: str
    severity: str
    category: Optional[str]
    resource: Optional[str]
    message: str
    suggested_fix: Optional[str]
    status: str
    dismiss_reason: Optional[str] = None
    session_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AuditFindingListResponse(BaseModel):
    items: list[AuditFindingResponse]
    total: int


class AuditRunDetailResponse(BaseModel):
    run: AuditRunResponse
    findings: list[AuditFindingResponse]


class AuditFindingDismissRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=2000)


class AuditFindingRemediateResponse(BaseModel):
    finding_id: uuid.UUID
    session_id: uuid.UUID
    status: str
