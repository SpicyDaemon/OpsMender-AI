"""Pydantic request/response schemas for the OpsMender API.

All schemas live in one file to avoid circular imports and make it easy
to see the full API surface at a glance.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
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
    auth_source: str = "local"
    role: str
    is_active: bool
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_color: Optional[str] = None
    must_change_password: bool = False
    primary_org_id: Optional[uuid.UUID] = None
    created_at: datetime
    # Sprint 56: soft-delete marker. When set, the user is hidden from
    # active lists; clicking through to a per-user page should render
    # the deleted-state placeholder rather than the editable detail.
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int


class UserCreateRequest(BaseModel):
    """Admin creates a local user directly (no invite link required).

    The password is temporary: by default the user is forced to change it on
    first login (``require_password_change``).
    """

    username: str = Field(..., min_length=3, max_length=150)
    email: str = Field(..., max_length=255)
    role: str = Field(..., pattern="^(admin|operator|viewer)$")
    password: str = Field(..., min_length=8)
    is_active: bool = True
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    require_password_change: bool = True


class TemporaryPasswordResponse(BaseModel):
    """One-time temporary password shown to the admin after a manual reset."""

    user_id: uuid.UUID
    temporary_password: str
    must_change_password: bool = True


# ---------------------------------------------------------------------------
# Sprint 56 — People surface
# ---------------------------------------------------------------------------


class UserUpdateRequest(BaseModel):
    """Admin-only patch of a user's role, active state, and profile fields."""

    role: Optional[str] = Field(default=None, pattern="^(admin|operator|viewer)$")
    is_active: Optional[bool] = None
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)


class MeUpdateRequest(BaseModel):
    """Self-service profile edit for the current user."""

    username: Optional[str] = Field(default=None, min_length=3, max_length=150)
    email: Optional[str] = Field(default=None, max_length=255)
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    avatar_color: Optional[str] = Field(default=None, max_length=20)


class MePasswordChangeRequest(BaseModel):
    """Self-service password change — verify current, set new."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class PasswordResetMintResponse(BaseModel):
    """Returned to the admin when minting a reset URL.

    The raw URL is shown exactly once. ``email_sent`` is True only when
    SMTP is configured AND the message was successfully handed to the
    server; on any failure the admin still has the copy-paste URL.
    """

    url: str
    expires_at: datetime
    email_sent: bool = False
    email_error: Optional[str] = None


class PasswordResetConsumeRequest(BaseModel):
    """Public — recipient consumes the token by setting a new password."""

    password: str = Field(..., min_length=8)


class SoftDeletePreconditions(BaseModel):
    """Surfaces blockers before the admin commits to the delete."""

    is_active: bool
    roster_memberships: int
    can_delete: bool


# --- Invites (Sprint 56 Step 4) ---


class InviteCreateRequest(BaseModel):
    """Admin mints a new org invite."""

    email: str = Field(..., max_length=255)
    role: str = Field(..., pattern="^(admin|operator|viewer)$")
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)


class InviteResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    role: str
    invited_by_user_id: Optional[uuid.UUID] = None
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    # Derived status the UI groups by: pending | accepted | expired | revoked.
    status: str

    model_config = {"from_attributes": True}


class InviteListResponse(BaseModel):
    items: list[InviteResponse]
    total: int


class InviteCreatedResponse(BaseModel):
    """Admin-facing response after minting a new invite.

    ``url`` is the one-time accept link — shown once. ``email_sent`` is
    True only when SMTP is configured AND the message was successfully
    handed to the server.
    """

    invite: InviteResponse
    url: str
    email_sent: bool = False
    email_error: Optional[str] = None


class InvitePublicResponse(BaseModel):
    """Returned to the recipient when they GET /invites/{token}.

    Deliberately limited: only the fields the accept page needs to
    render (org name, email the invite was sent to, role they'll get,
    expiry). No internal IDs or token data.
    """

    email: str
    role: str
    org_name: str
    expires_at: datetime
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class InviteAcceptRequest(BaseModel):
    """Recipient consumes the token by setting username + password (+ names)."""

    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8)
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Optional[str] = Field(
        default=None, pattern="^(critical|high|medium|low)$"
    )
    service_id: Optional[uuid.UUID] = None
    external_id: Optional[str] = Field(default=None, max_length=500)
    external_source: Optional[str] = Field(default=None, max_length=100)


class IncidentUpdate(BaseModel):
    status: Optional[str] = Field(
        default=None, pattern="^(open|in_progress|resolved)$"
    )
    severity: Optional[str] = Field(
        default=None, pattern="^(critical|high|medium|low)$"
    )
    service_id: Optional[uuid.UUID] = None
    service_id_set: bool = False
    handoff_reason: Optional[str] = Field(default=None, max_length=500)


class IncidentResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    severity: Optional[str]
    service_id: Optional[uuid.UUID] = None
    ingestion_model_config_id: Optional[uuid.UUID] = None
    service_name: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    team_name: Optional[str] = None
    external_id: Optional[str] = None
    external_source: Optional[str] = None
    # Combine incidents (v1.2): set on a secondary that was folded into a
    # primary (status then == "merged").
    merged_into_incident_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    # First-acknowledgment timestamp (MTTA). Null until someone takes/acks the
    # incident; set once and never overwritten.
    acknowledged_at: Optional[datetime] = None

    # -- Responder / assignment state (Part 6) -----------------------------
    # Computed by the route so the list doesn't infer from detail-only state.
    # responder_state: awaiting | assigned | escalated | unassigned.
    # A *_display_name of null with a non-null *_user_id means the user was
    # deleted (frontend renders "Deleted user <id>").
    responder_user_id: Optional[uuid.UUID] = None
    responder_display_name: Optional[str] = None
    responder_email: Optional[str] = None
    responder_state: str = "unassigned"
    acknowledged_by_user_id: Optional[uuid.UUID] = None
    acknowledged_by_display_name: Optional[str] = None
    escalated_to_user_id: Optional[uuid.UUID] = None
    escalated_to_display_name: Optional[str] = None

    # -- AI session state --------------------------------------------------
    # Computed by the route from the incident's linked sessions so the list can
    # show an "AI in progress" indicator without a detail fetch.
    # ai_session_active is True when a session is `active` or `awaiting_approval`;
    # ai_session_status carries the representative session status (an in-progress
    # session wins, otherwise the latest by start time), or null when the
    # incident has never had a session.
    ai_session_active: bool = False
    ai_session_status: Optional[str] = None

    model_config = {"from_attributes": True}


class FireTestIncidentRequest(BaseModel):
    service_id: Optional[uuid.UUID] = None


class FireTestIncidentResponse(BaseModel):
    incident: IncidentResponse
    resolved_tier: int
    auto_start_status: str = Field(pattern="^(queued|skipped|failed)$")
    auto_start_reason: Optional[str] = None
    message: str


class IncidentCreateResponse(IncidentResponse):
    """Manual incident creation result.

    Extends the incident record (so ``id`` etc. stay top-level) with the AI
    auto-start outcome. ``auto_start_status``: ``queued`` (a T0 session was
    scheduled), ``skipped`` (T1/T2 — waits for acknowledgment), or ``failed``
    (e.g. no model configured).
    """

    resolved_tier: int
    auto_start_status: str = Field(pattern="^(queued|skipped|failed)$")
    auto_start_reason: Optional[str] = None
    auto_start_message: str


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int


class IncidentCombineRequest(BaseModel):
    """Fold one or more secondary incidents into a primary (incident merge)."""

    secondary_ids: list[uuid.UUID] = Field(..., min_length=1)
    note: Optional[str] = Field(default=None, max_length=2000)


class IncidentCombineResponse(BaseModel):
    primary: IncidentResponse
    merged_incident_ids: list[uuid.UUID]
    moved_comments: int
    stopped_sessions: int


# Sprint 61 Step 4 — postmortem authoring surface. The default template
# is returned alongside the stored markdown so a fresh incident's editor
# can prefill the section headings without the frontend hardcoding the
# canonical structure.
DEFAULT_POSTMORTEM_TEMPLATE = """## Summary
Briefly describe what happened and the user-visible impact.

## Impact
Who was affected, for how long, and how badly.

## Timeline
- HH:MM UTC — first signal
- HH:MM UTC — acknowledged
- HH:MM UTC — mitigated
- HH:MM UTC — fully resolved

## Root cause
What was the underlying technical cause.

## Resolution
What you changed to stop the bleeding, and what's still in flight.

## Lessons learned
What worked, what didn't, what to change for next time.

## Memory candidates
Short, durable lessons to save into OpsMender memory so future sessions benefit. One bullet per memory.
"""


class IncidentPostmortemResponse(BaseModel):
    incident_id: uuid.UUID
    postmortem_md: Optional[str]
    postmortem_updated_at: Optional[datetime]
    template: str = DEFAULT_POSTMORTEM_TEMPLATE


class IncidentPostmortemUpdate(BaseModel):
    postmortem_md: Optional[str] = Field(default=None, max_length=100_000)


class IncidentCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class IncidentCommentResponse(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    body: str
    author_user_id: Optional[uuid.UUID] = None
    author_label: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class IncidentCommentListResponse(BaseModel):
    items: list[IncidentCommentResponse]
    total: int


class PostmortemMemoryCandidate(BaseModel):
    """One memory created (or skipped) from the postmortem's candidates."""

    memory_id: Optional[uuid.UUID] = None
    title: str
    created: bool


class PostmortemMemoryCandidatesResponse(BaseModel):
    """Result of turning a postmortem's Memory-candidates bullets into memories."""

    created: int = 0
    skipped: int = 0
    items: list[PostmortemMemoryCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    incident_id: Optional[uuid.UUID] = None
    workflow_profile_id: Optional[uuid.UUID] = None
    agent_team_profile_id: Optional[uuid.UUID] = None
    # AI Autonomy Tier: 0 Autonomous · 1 Approval Required · 2 Advisory Only.
    # When omitted, the resolver applies service → skill → org → Tier 2.
    tier: Optional[int] = Field(default=None, ge=0, le=2)
    model_provider: Optional[str] = None
    model_id: Optional[str] = None
    initial_briefing: Optional[str] = Field(default=None, max_length=10000)


class SessionOverrideRequest(BaseModel):
    """Intercept a running session: stop the AI and continue under operator
    control at a less-autonomous tier (Tier 1 Approval Required or Tier 2
    Advisory Only). Cannot override into Tier 0."""

    tier: int = Field(..., ge=1, le=2)


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
    resolution_note: Optional[str] = None
    requested_at: datetime
    resolved_at: Optional[datetime]
    resolved_by: Optional[uuid.UUID]
    expires_at: datetime

    model_config = {"from_attributes": True}


class ApprovalRedirectRequest(BaseModel):
    """Tier 1 redirect — free-text steering the AI folds into its next plan."""

    guidance: str = Field(..., min_length=1, max_length=4000)


class ApprovalListResponse(BaseModel):
    items: list[ApprovalRequestResponse]
    total: int


# ---------------------------------------------------------------------------
# In-app notifications (v1.2 — notification center / bell)
# ---------------------------------------------------------------------------


class InAppNotificationResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    category: str
    title: str
    body: Optional[str] = None
    link: Optional[str] = None
    incident_id: Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class InAppNotificationListResponse(BaseModel):
    items: list[InAppNotificationResponse]
    total: int
    unread: int


class UnreadCountResponse(BaseModel):
    unread: int


class MarkReadResponse(BaseModel):
    updated: int


class QuietHours(BaseModel):
    enabled: bool = False
    start: Optional[str] = None  # "HH:MM"
    end: Optional[str] = None  # "HH:MM"
    tz: str = "UTC"


class NotificationPreferencesResponse(BaseModel):
    # Categories the user has muted (no in-app notification is created).
    muted_categories: list[str] = Field(default_factory=list)
    quiet_hours: Optional[QuietHours] = None
    # All mutable categories, so the UI can render a toggle per category.
    categories: list[str] = Field(default_factory=list)


class NotificationPreferencesUpdate(BaseModel):
    muted_categories: Optional[list[str]] = None
    quiet_hours: Optional[QuietHours] = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    tier: int
    mcp_servers: list[dict[str, Any]]
    audit_output: str
    logging_level: str
    # Sprint 56: surface the deployment-level booleans the UI needs to
    # render the People surface correctly. Read from env at process
    # start; not editable from the UI.
    multi_org_enabled: bool = False
    smtp_configured: bool = False
    # Sprint 64: visibility flags for the simple-by-default auth UX.
    # ``advanced_auth_enabled`` (env-driven) and the two
    # ``*_configured`` booleans (per-tenant DB lookup) together let the
    # frontend decide whether to render SSO/SAML admin surfaces.
    # Frontend rule: show advanced auth settings when
    # ``advanced_auth_enabled || sso_configured || saml_configured``.
    advanced_auth_enabled: bool = False
    sso_configured: bool = False
    saml_configured: bool = False
    # v1 paging: the absolute base URL the frontend uses to render a
    # service's full alert intake URL. Sourced from
    # ``OPSMENDER_PUBLIC_BASE_URL``; null when unset, in which case the
    # browser falls back to ``window.location.origin``.
    public_base_url: Optional[str] = None


class SetupChecklistResponse(BaseModel):
    """First-run setup checklist state (Sprint 43 P0 #1).

    Each flag indicates whether the org has completed that setup step.
    The frontend renders the checklist when ``all_complete`` is false
    and hides it otherwise. ``paging_service_added`` is marked optional
    so a fresh org with the other four checked off is considered done.
    """

    model_configured: bool
    mcp_server_added: bool
    skill_defined: bool
    ingest_token_created: bool
    paging_service_added: bool
    all_complete: bool


class ConfigUpdate(BaseModel):
    # Default AI Autonomy Tier: 0 Autonomous · 1 Approval Required · 2 Advisory.
    tier: Optional[int] = Field(default=None, ge=0, le=2)
    logging_level: Optional[str] = Field(
        default=None,
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        description=(
            "Python logging level. Higher levels suppress lower-priority records. "
            "DEBUG keeps everything; CRITICAL only keeps fatal events."
        ),
    )


class ModelConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model_id: str
    api_key_env_var: Optional[str]
    base_url: Optional[str]
    api_version: Optional[str]
    provider_meta: Optional[dict[str, str]]
    max_tokens: int
    temperature: float
    is_default: bool
    is_active: bool = True
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


class ModelConfigTestResponse(BaseModel):
    """Result of a live connection test against a saved model config."""

    ok: bool
    latency_ms: Optional[int] = None
    detail: Optional[str] = None  # short success note (e.g. echoed response head)
    error: Optional[str] = None  # failure reason when ok is False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: str = Field(
        pattern="^(anthropic|openai|azure_openai|bedrock|vertex_ai|ollama|openai_compatible)$"
    )
    model_id: str = Field(..., min_length=1, max_length=200)
    api_key_env_var: Optional[str] = Field(default=None, max_length=100)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_version: Optional[str] = Field(default=None, max_length=50)
    provider_meta: Optional[dict[str, str]] = None
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
    # OAuth 2.1 connection status: "connected" when a valid token row exists;
    # "reconnect_needed" when the token expired with no refresh token; None
    # when no OAuth credentials have been obtained for this server.
    oauth_status: Optional[str] = None


class MCPServerListResponse(BaseModel):
    items: list[MCPServerResponse]
    total: int


class MCPServerStatusResponse(BaseModel):
    server_id: uuid.UUID
    status: str
    last_successful_call_at: Optional[datetime] = None
    last_error: Optional[str] = None


class MCPServerStatusListResponse(BaseModel):
    items: list[MCPServerStatusResponse]
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


_ASSIGNMENT_PATTERN = "^(unassigned|global|server)$"


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    mcp_server_id: Optional[uuid.UUID]
    assignment: str = "global"
    content_md: str
    focus_areas: list[str] = Field(default_factory=list)
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
    assignment: Optional[str] = Field(default=None, pattern=_ASSIGNMENT_PATTERN)


class SkillUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    content_md: str = Field(..., min_length=1)
    description: Optional[str] = None
    mcp_server_id: Optional[uuid.UUID] = None
    assignment: Optional[str] = Field(default=None, pattern=_ASSIGNMENT_PATTERN)


class SkillCloneRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    mcp_server_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    assignment: Optional[str] = Field(default=None, pattern=_ASSIGNMENT_PATTERN)


class SkillTemplateResponse(BaseModel):
    """A fresh 3-tier MCP Skill template (not yet saved)."""

    name: str
    content_md: str


class SkillDiscoverRequest(BaseModel):
    """Discover an MCP server's tools for the Skill Studio generator."""

    mcp_server_id: uuid.UUID


class SkillDiscoveredTool(BaseModel):
    """One discovered MCP tool with a heuristic classification suggestion."""

    name: str
    description: Optional[str] = None
    suggested_classification: str  # "safe" | "caution" | "destructive"
    generic: bool = False
    suggested_deny: bool = False
    needs_review: bool = False
    rationale: str = ""


class SkillDiscoverResponse(BaseModel):
    mcp_server_id: uuid.UUID
    mcp_server_name: str
    tools: list[SkillDiscoveredTool] = Field(default_factory=list)


class SkillGenerateOperation(BaseModel):
    """An operator-reviewed classification for one tool, fed to the generator."""

    tool: str = Field(..., min_length=1, max_length=200)
    classification: str = Field(default="safe", pattern=r"^(safe|caution|destructive)$")
    deny: bool = False
    allow_generic: bool = False
    reversible: Optional[bool] = None
    # Tool that undoes this one, with the same parameters. Required (together
    # with reversible=true) for a non-``safe`` tool to clear the Tier 0 safety
    # floor — see backend/skills/parser.py::tier0_violation_reason.
    compensating_inverse: Optional[str] = None
    notes: Optional[str] = None


class SkillGenerateRequest(BaseModel):
    """Build (but do not save) an MCP Skill draft from classified tools."""

    name: str = Field(default="New MCP Skill (generated)", min_length=1, max_length=150)
    description: Optional[str] = None
    environment: str = Field(default="your-environment", max_length=120)
    operations: list[SkillGenerateOperation] = Field(default_factory=list)
    tier0_instructions: str = ""
    tier1_instructions: str = ""
    tier2_instructions: str = ""


class SkillGenerateResponse(BaseModel):
    """A generated MCP Skill draft (not yet saved)."""

    name: str
    content_md: str


class SkillAISuggestToolInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class SkillAISuggestRequest(BaseModel):
    """Ask the configured model to classify discovered tools + author prose."""

    intent: str = Field(default="", max_length=4000)
    environment: str = Field(default="your-environment", max_length=120)
    tools: list[SkillAISuggestToolInput] = Field(default_factory=list)


class SkillAISuggestedTool(BaseModel):
    name: str
    classification: str
    deny: bool = False
    allow_generic: bool = False
    reversible: Optional[bool] = None
    compensating_inverse: Optional[str] = None
    generic: bool = False
    needs_review: bool = False
    rationale: str = ""


class SkillAISuggestResponse(BaseModel):
    tools: list[SkillAISuggestedTool] = Field(default_factory=list)
    tier0_instructions: str = ""
    tier1_instructions: str = ""
    tier2_instructions: str = ""
    environment: str = "your-environment"


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
# Organization email settings + incident reports
# ---------------------------------------------------------------------------


class OrgEmailSettingsUpsert(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    security: str = Field(default="starttls", pattern="^(starttls|ssl|none)$")
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = None
    clear_password: bool = False
    from_name: Optional[str] = Field(default="OpsMender", max_length=255)
    from_address: str = Field(..., min_length=3, max_length=255)


class OrgEmailSettingsResponse(BaseModel):
    org_id: uuid.UUID
    host: str
    port: int
    security: str
    username: Optional[str]
    from_name: Optional[str]
    from_address: str
    has_password: bool
    source: str = "database"


class EmailSettingsTestRequest(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=255)


class EmailSettingsTestResponse(BaseModel):
    success: bool
    detail: str


class ReportScheduleUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    cadence: str = Field(pattern="^(weekly|monthly|quarterly)$")
    recipients: list[str] = Field(..., min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    format: str = Field(default="pdf", pattern="^(csv|pdf)$")
    next_run_at: datetime
    enabled: bool = True


class ReportScheduleResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    cadence: str
    recipients: list[str]
    filters: dict[str, Any]
    format: str
    next_run_at: datetime
    enabled: bool
    last_run_at: Optional[datetime]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportScheduleListResponse(BaseModel):
    items: list[ReportScheduleResponse]
    total: int


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


class SessionProfileTemplate(BaseModel):
    """A built-in Session Profile preset (a starting point, not yet saved)."""

    key: str
    name: str
    description: str
    node_order: list[str]


class SessionProfileTemplateListResponse(BaseModel):
    items: list[SessionProfileTemplate]
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
        pattern="^(auto|cloudwatch|azure_monitor|gcp_monitoring|oci_monitoring|generic)$",
    )
    # Optional sample payload from the source tool — if supplied, the
    # server parses it on create so future payloads with the same shape
    # skip the LLM fallback.
    sample_payload: Optional[dict] = None
    # Optional service to bind this token to. When set, every incident
    # created through this token gets ``service_id`` pre-filled so the
    # paging engine routes to the owning team automatically.
    service_id: Optional[uuid.UUID] = None


class IngestTokenResponse(BaseModel):
    """Returned on list/get — never exposes the raw token."""

    id: uuid.UUID
    name: str
    provider: str
    is_active: bool
    service_id: Optional[uuid.UUID] = None
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
# SLA Targets (Sprint 25)
# ---------------------------------------------------------------------------


class SLATargetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., pattern="^(http|tcp|external)$")
    config: Optional[dict[str, Any]] = None
    owner_team: Optional[str] = Field(default=None, max_length=100)
    service_id: Optional[uuid.UUID] = None
    is_active: bool = True


class SLATargetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    kind: Optional[str] = Field(None, pattern="^(http|tcp|external)$")
    config: Optional[dict[str, Any]] = None
    owner_team: Optional[str] = Field(default=None, max_length=100)
    service_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None


class SLATargetResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    config: Optional[dict[str, Any]]
    owner_team: Optional[str]
    service_id: Optional[uuid.UUID] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # -- Computed convenience fields (Reliability v1) ----------------------
    # Populated by the route; defaulted so model_validate(orm_row) still works.
    url: Optional[str] = None
    monitor_type: Optional[str] = None  # "http" | "https" (derived from URL)
    current_status: str = "unknown"  # "up" | "down" | "unknown"
    last_check_at: Optional[datetime] = None
    uptime_30d_pct: Optional[float] = None
    active_slo_count: int = 0
    # Resolved from service_id (Phase 6) so the UI can show the owning service/team.
    service_name: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    team_name: Optional[str] = None

    model_config = {"from_attributes": True}


class SLATargetListResponse(BaseModel):
    items: list[SLATargetResponse]
    total: int


class SLASummaryResponse(BaseModel):
    """Org-level rollup for the Reliability page summary row."""

    total_targets: int
    targets_up: int
    targets_down: int
    targets_unknown: int
    avg_uptime_30d_pct: Optional[float] = None
    active_slo_warnings: int = 0


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


class SLORecommendation(BaseModel):
    """An advisory recommendation for a breaching / at-risk SLO.

    Read-only guidance only — recommendations never create incidents or page
    anyone automatically (that stays an operator decision, per ROADMAP).
    """

    slo_id: uuid.UUID
    slo_name: str
    target_id: uuid.UUID
    target_name: str
    severity: str  # "critical" | "warning"
    objective_pct: float
    actual_pct: float
    error_budget_remaining_pct: float
    burn_rate: float
    target_status: str  # "up" | "down" | "unknown"
    # Owning service (when the target is linked) so the recommendation is
    # actionable / routable.
    service_id: Optional[uuid.UUID] = None
    service_name: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    team_name: Optional[str] = None
    headline: str
    actions: list[str]


class SLORecommendationsResponse(BaseModel):
    items: list[SLORecommendation]
    total: int
    generated_at: datetime


# ---------------------------------------------------------------------------
# Maintenance Windows (Sprint 25)
# ---------------------------------------------------------------------------


class MaintenanceWindowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    reason: Optional[str] = None
    description: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    rrule: Optional[str] = None
    target_ids: list[str] = Field(default_factory=list)
    scope_type: str = Field(default="global", pattern="^(global|service|roster|team)$")
    scope_id: Optional[uuid.UUID] = None
    scope_ids: list[uuid.UUID] = Field(default_factory=list)


class MaintenanceWindowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    reason: Optional[str] = None
    description: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    rrule: Optional[str] = None
    target_ids: Optional[list[str]] = None
    scope_type: Optional[str] = Field(
        None, pattern="^(global|service|roster|team)$"
    )
    scope_id: Optional[uuid.UUID] = None
    scope_ids: Optional[list[uuid.UUID]] = None


class MaintenanceWindowResponse(BaseModel):
    id: uuid.UUID
    name: str
    reason: Optional[str]
    description: Optional[str]
    starts_at: datetime
    ends_at: datetime
    rrule: Optional[str]
    target_ids: list[str]
    scope_type: str
    scope_id: Optional[uuid.UUID]
    scope_ids: list[uuid.UUID] = Field(default_factory=list)
    created_by: Optional[uuid.UUID]
    created_at: datetime
    approved: bool = True
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MaintenanceWindowListResponse(BaseModel):
    items: list[MaintenanceWindowResponse]
    total: int


# ---------------------------------------------------------------------------
# User notification preferences (Sprint 35)
# ---------------------------------------------------------------------------


class UserNotificationPrefResponse(BaseModel):
    user_id: uuid.UUID
    org_id: uuid.UUID
    channels: dict
    routing: dict
    quiet_hours: Optional[dict]
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserNotificationPrefUpdate(BaseModel):
    channels: Optional[dict] = None
    routing: Optional[dict] = None
    quiet_hours: Optional[dict] = None


# ---------------------------------------------------------------------------
# Organization notification settings (Sprint 35)
# ---------------------------------------------------------------------------


class NotificationSettingsResponse(BaseModel):
    org_id: uuid.UUID
    notification_dedup_window_minutes: int
    slack_incident_channels_enabled: bool = False


class NotificationSettingsUpdate(BaseModel):
    notification_dedup_window_minutes: int | None = Field(default=None, ge=0, le=1440)
    slack_incident_channels_enabled: bool | None = None


# ---------------------------------------------------------------------------
# Uptime queries (Sprint 25)
# ---------------------------------------------------------------------------


class UptimeSeriesPoint(BaseModel):
    ts: datetime
    up_pct: float
    status: str = "unknown"  # "up" | "down" | "unknown"


class UptimeEpisode(BaseModel):
    """A discrete outage episode (a run of down probes) for the outage history.

    ``maintenance`` episodes fell inside a maintenance window — shown for
    visibility but excluded from the SLA/SLO uptime math.
    """

    started_at: datetime
    ended_at: Optional[datetime] = None  # None == still ongoing
    duration_seconds: int
    maintenance: bool = False


class UptimeResponse(BaseModel):
    """Aggregated uptime statistics for a target over a window."""

    target_id: uuid.UUID
    uptime_pct: float
    total_samples: int
    up_samples: int
    downtime_seconds: int
    suppressed_seconds: int
    # Reliability v1 additions:
    mtbf_seconds: Optional[float] = None
    down_events: int = 0
    series: list[UptimeSeriesPoint] = Field(default_factory=list)
    # Outage history (v1.2): discrete down episodes for the table.
    episodes: list[UptimeEpisode] = Field(default_factory=list)


class ResponseTimeSeriesPoint(BaseModel):
    ts: datetime
    avg_latency_ms: Optional[float] = None
    min_latency_ms: Optional[int] = None
    max_latency_ms: Optional[int] = None
    samples: int = 0


class ResponseTimeResponse(BaseModel):
    """Latency statistics and fixed-width chart series for an SLA target."""

    target_id: uuid.UUID
    window: str
    avg_latency_ms: Optional[float] = None
    min_latency_ms: Optional[int] = None
    max_latency_ms: Optional[int] = None
    total_samples: int = 0
    series: list[ResponseTimeSeriesPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bot connectors (Sprint 27)
# ---------------------------------------------------------------------------


class BotConnectorUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    platform: str = Field(pattern="^(telegram|signal|whatsapp|slack|discord|teams|mattermost|matrix|feishu|dingtalk|wecom|weixin|twilio|email|smtp|homeassistant|bluebubbles|eventbridge|custom)$")
    config: Optional[dict] = None
    credentials: Optional[dict] = None
    clear_credentials: bool = False
    allowed_capabilities: list[str] = Field(..., min_length=1)
    lanes: list[str] = Field(default_factory=lambda: ["respond"])
    team_scope: str = Field(default="workspace", pattern="^(workspace|teams)$")
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    status: str = Field(
        default="not_configured",
        pattern="^(not_configured|configured|healthy|error|disabled)$",
    )
    is_enabled: bool = False
    native_actions_enabled: bool = False


class BotConnectorResponse(BaseModel):
    id: uuid.UUID
    name: str
    platform: str
    config: Optional[dict]
    allowed_capabilities: list[str]
    lanes: list[str] = Field(default_factory=lambda: ["respond"])
    status: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    last_checked_at: Optional[datetime]
    last_error: Optional[str]
    credential_keys: list[str] = Field(default_factory=list)
    has_credentials: bool
    # User-friendly platform name (e.g. "Twilio (SMS)") and the honest
    # capability descriptor for this connector's platform.
    platform_label: Optional[str] = None
    platform_capabilities: Optional[dict] = None
    team_scope: str = "workspace"
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    team_names: list[str] = Field(default_factory=list)
    native_actions_enabled: bool = False
    callback_status: str = "not_configured"
    callback_last_verified_at: Optional[datetime] = None
    callback_last_error: Optional[str] = None

    model_config = {"from_attributes": True}


class BotConnectorListResponse(BaseModel):
    items: list[BotConnectorResponse]
    total: int


class BotConnectorTestCheck(BaseModel):
    """One structured check in a Notification Channel test result."""

    name: str
    level: str  # "pass" | "warn" | "fail"
    detail: str


class BotConnectorTestRequest(BaseModel):
    """Optional body for the connector test.

    ``live=False`` (default) runs configuration/readiness checks only — no
    network call, no message sent. ``live=True`` additionally verifies the
    provider connection (where the adapter supports it) and **sends a real test
    message** to ``chat_id`` (or the channel's configured destination).
    """

    live: bool = False
    chat_id: Optional[str] = None


class BotConnectorTestResponse(BaseModel):
    success: bool
    detail: str
    status: str
    checks: list[BotConnectorTestCheck] = Field(default_factory=list)
    live_message_sent: bool = False
    target_chat_id: Optional[str] = None


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
    # User-friendly name + honest capability descriptor (incident_card,
    # interactive_actions, delivery_only, …) for this platform.
    label: Optional[str] = None
    capabilities: Optional[dict] = None


class BotConnectorPlatformListResponse(BaseModel):
    items: list[BotConnectorPlatformSchema]
    total: int


class BotUserLinkCreate(BaseModel):
    platform_user_id: str = Field(..., min_length=1, max_length=120)
    external_username: Optional[str] = Field(default=None, max_length=200)
    external_display_name: Optional[str] = Field(default=None, max_length=200)
    opsmender_user_id: uuid.UUID


class BotUserLinkResponse(BaseModel):
    id: uuid.UUID
    connector_id: uuid.UUID
    platform_user_id: str
    external_username: Optional[str] = None
    external_display_name: Optional[str] = None
    opsmender_user_id: uuid.UUID
    opsmender_username: str
    opsmender_role: str
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    verified: bool = True


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


class AuditScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    analyzers: list[str] = Field(..., min_length=1)
    mcp_server_name: Optional[str] = Field(None, max_length=200)
    focus_areas: list[str] = Field(default_factory=list)
    interval_minutes: int = Field(..., ge=15, le=43200)  # 15 min .. 30 days
    is_active: bool = True


class AuditScheduleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    analyzers: Optional[list[str]] = Field(None, min_length=1)
    mcp_server_name: Optional[str] = Field(None, max_length=200)
    focus_areas: Optional[list[str]] = None
    interval_minutes: Optional[int] = Field(None, ge=15, le=43200)
    is_active: Optional[bool] = None


class AuditScheduleResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: Optional[str] = None
    analyzers: list[str]
    mcp_server_name: Optional[str] = None
    focus_areas: list[str] = Field(default_factory=list)
    interval_minutes: int
    is_active: bool
    last_run_at: Optional[datetime] = None
    next_run_at: datetime
    created_by: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditScheduleListResponse(BaseModel):
    items: list[AuditScheduleResponse]
    total: int


class AuditFindingRemediateResponse(BaseModel):
    finding_id: uuid.UUID
    session_id: uuid.UUID
    status: str


# ---------------------------------------------------------------------------
# Paging (Sprint 33) — teams / services / rosters / priority rules / incident
# assignments. See docs/paging-model.md for the data model.
# ---------------------------------------------------------------------------


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamListResponse(BaseModel):
    items: list[TeamResponse]
    total: int


class TeamMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default="member", pattern="^(member|lead)$")


class TeamMemberResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    added_at: datetime

    model_config = {"from_attributes": True}


class TeamMemberListResponse(BaseModel):
    items: list[TeamMemberResponse]
    total: int


class ServiceCreate(BaseModel):
    team_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    priority: str = Field(default="P2", pattern="^(P0|P1|P2|P3)$")
    preferred_mcp_server_ids: list[uuid.UUID] = Field(default_factory=list)
    preferred_model_config_ids: list[uuid.UUID] = Field(default_factory=list, max_length=3)
    ai_default_tier: Optional[int] = Field(default=None, ge=0, le=2)
    external_refs: Optional[dict[str, Any]] = None
    is_active: bool = True


class ServiceUpdate(BaseModel):
    team_id: Optional[uuid.UUID] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    priority: Optional[str] = Field(None, pattern="^(P0|P1|P2|P3)$")
    preferred_mcp_server_ids: Optional[list[uuid.UUID]] = None
    preferred_model_config_ids: Optional[list[uuid.UUID]] = Field(
        default=None, max_length=3
    )
    ai_default_tier: Optional[int] = Field(default=None, ge=0, le=2)
    external_refs: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class ServiceResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    priority: str
    preferred_mcp_server_ids: list[uuid.UUID] = Field(default_factory=list)
    preferred_model_config_ids: list[uuid.UUID] = Field(default_factory=list)
    ai_default_tier: Optional[int] = None
    intake_url: Optional[str] = None
    external_refs: Optional[dict[str, Any]]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]
    total: int


class RosterCreate(BaseModel):
    team_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    time_zone: str = Field(default="UTC", max_length=64)
    pattern: str = Field(default="weekly", pattern="^(weekly|daily|custom_n_days)$")
    pattern_length: int = Field(default=7, ge=1, le=365)
    coverage_start_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    coverage_end_time: str = Field(default="17:00", pattern=r"^\d{2}:\d{2}$")
    handoff_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    handoff_day: Optional[str] = None
    anchor_date: date
    is_active: bool = True


class RosterUpdate(BaseModel):
    team_id: Optional[uuid.UUID] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    time_zone: Optional[str] = Field(None, max_length=64)
    pattern: Optional[str] = Field(None, pattern="^(weekly|daily|custom_n_days)$")
    pattern_length: Optional[int] = Field(None, ge=1, le=365)
    coverage_start_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    coverage_end_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    handoff_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    handoff_day: Optional[str] = None
    anchor_date: Optional[date] = None
    is_active: Optional[bool] = None


class RosterResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    description: Optional[str]
    time_zone: str
    pattern: str
    pattern_length: int
    coverage_start_time: str
    coverage_end_time: str
    handoff_time: str
    handoff_day: Optional[str]
    anchor_date: date
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RosterListResponse(BaseModel):
    items: list[RosterResponse]
    total: int


class RosterMemberAdd(BaseModel):
    user_id: uuid.UUID
    position_index: int = Field(..., ge=0)


class RosterMemberResponse(BaseModel):
    id: uuid.UUID
    roster_id: uuid.UUID
    user_id: uuid.UUID
    position_index: int
    added_at: datetime

    model_config = {"from_attributes": True}


class RosterMemberListResponse(BaseModel):
    items: list[RosterMemberResponse]
    total: int


class RosterReorderRequest(BaseModel):
    ordered_user_ids: list[uuid.UUID] = Field(..., min_length=1)


class RosterOverrideCreate(BaseModel):
    covering_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = Field(None, max_length=2000)


class RosterOverrideResponse(BaseModel):
    id: uuid.UUID
    roster_id: uuid.UUID
    covering_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str]
    created_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class RosterOverrideListResponse(BaseModel):
    items: list[RosterOverrideResponse]
    total: int


class OnCallResolveResponse(BaseModel):
    roster_id: uuid.UUID
    at: datetime
    user_id: Optional[uuid.UUID]


class OnCallRangeItem(BaseModel):
    at: datetime
    user_id: Optional[uuid.UUID]
    is_override: bool = False
    override_id: Optional[uuid.UUID] = None


class OnCallRangeResponse(BaseModel):
    roster_id: uuid.UUID
    from_at: datetime
    to_at: datetime
    step_hours: int
    items: list[OnCallRangeItem]


class PriorityRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    rule_index: int = Field(default=0, ge=0)
    condition: dict[str, Any]
    priority: str = Field(..., pattern="^(P0|P1|P2|P3)$")
    response_mode: Optional[str] = Field(
        None, pattern="^(auto_resolve|notify|page|escalate_immediate)$"
    )
    is_active: bool = True


class PriorityRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    rule_index: Optional[int] = Field(None, ge=0)
    condition: Optional[dict[str, Any]] = None
    priority: Optional[str] = Field(None, pattern="^(P0|P1|P2|P3)$")
    response_mode: Optional[str] = Field(
        None, pattern="^(auto_resolve|notify|page|escalate_immediate)$"
    )
    is_active: Optional[bool] = None


class PriorityRuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    rule_index: int
    condition: dict[str, Any]
    priority: str
    response_mode: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PriorityRuleListResponse(BaseModel):
    items: list[PriorityRuleResponse]
    total: int


class IncidentAssignmentResponse(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    assigned_to: uuid.UUID
    assigned_by: str
    assigned_at: datetime
    released_at: Optional[datetime]

    model_config = {"from_attributes": True}


class IncidentAssignRequest(BaseModel):
    user_id: Optional[uuid.UUID] = None  # null means self


class IncidentBulkActionRequest(BaseModel):
    """Sprint 50 — bulk action on a set of incidents.

    The ``action`` field is the discriminator. ``incident_ids`` is the set
    targeted; the route returns a summary of which ids succeeded and which
    failed, never aborting on the first error.
    """

    action: str = Field(
        ..., pattern="^(acknowledge|resolve|reopen|reassign|delete)$"
    )
    incident_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    # For action="reassign" (also used to set the assignee when acknowledging).
    user_id: Optional[uuid.UUID] = None


class IncidentBulkActionResult(BaseModel):
    incident_id: uuid.UUID
    ok: bool
    error: Optional[str] = None


class IncidentBulkActionResponse(BaseModel):
    action: str
    succeeded: int
    failed: int
    items: list[IncidentBulkActionResult]


class SuppressedByMaintenanceWindow(BaseModel):
    id: uuid.UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    scope_type: str


class IncidentPagingPanelResponse(BaseModel):
    incident_id: uuid.UUID
    priority: Optional[str]
    response_mode: Optional[str]
    service_id: Optional[uuid.UUID]
    assignment: Optional[IncidentAssignmentResponse]
    suppressed_by_maintenance_window: Optional[SuppressedByMaintenanceWindow] = None


class IncidentTimelineItemResponse(BaseModel):
    id: str
    happened_at: datetime
    lane: str
    event_type: str
    title: str
    body: Optional[str] = None
    actor_user_id: Optional[uuid.UUID] = None
    actor_label: Optional[str] = None
    status: Optional[str] = None
    session_id: Optional[uuid.UUID] = None
    session_label: Optional[str] = None
    session_tier: Optional[int] = None
    tool_name: Optional[str] = None
    safety_class: Optional[str] = None
    tier_decision: Optional[str] = None
    duration_ms: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
    json_payload: Optional[dict[str, Any]] = None


class IncidentTimelineResponse(BaseModel):
    items: list[IncidentTimelineItemResponse]
    total: int


# ---------------------------------------------------------------------------
# Escalation chains (Sprint 34)
# ---------------------------------------------------------------------------


class EscalationChainCreate(BaseModel):
    team_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    is_active: bool = True


class EscalationChainUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EscalationChainResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    description: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class EscalationChainListResponse(BaseModel):
    items: list[EscalationChainResponse]
    total: int


class EscalationStepCreate(BaseModel):
    step_index: int = Field(..., ge=0)
    target_type: str = Field(..., pattern="^(roster|user|team)$")
    target_id: uuid.UUID
    timeout_seconds: int = Field(default=300, ge=10, le=86400)
    notify_channels: Optional[dict[str, Any]] = None


class EscalationStepResponse(BaseModel):
    id: uuid.UUID
    chain_id: uuid.UUID
    step_index: int
    target_type: str
    target_id: uuid.UUID
    timeout_seconds: int
    notify_channels: Optional[dict[str, Any]]

    model_config = {"from_attributes": True}


class EscalationStepListResponse(BaseModel):
    items: list[EscalationStepResponse]
    total: int


class ServiceEscalationChainCreate(BaseModel):
    chain_id: uuid.UUID
    applies_when: Optional[dict[str, Any]] = None


class ServiceEscalationChainResponse(BaseModel):
    id: uuid.UUID
    service_id: uuid.UUID
    chain_id: uuid.UUID
    applies_when: Optional[dict[str, Any]]

    model_config = {"from_attributes": True}


class ServiceEscalationChainListResponse(BaseModel):
    items: list[ServiceEscalationChainResponse]
    total: int


class EscalationStepUpdate(BaseModel):
    """Sprint 49 — partial update for an escalation step."""

    timeout_seconds: Optional[int] = Field(default=None, ge=10, le=86400)
    notify_channels: Optional[dict[str, Any]] = None
    # When true, ``notify_channels`` is treated as the new value even if null
    # (lets the operator explicitly clear the channels map). Defaults to false
    # so an omitted field leaves the existing value untouched.
    notify_channels_set: bool = False


class EscalationStepReorderRequest(BaseModel):
    """Sprint 49 — drag-reorder a chain's steps."""

    step_ids: list[uuid.UUID] = Field(..., min_length=1)


class ChainWhereUsedItem(BaseModel):
    service_id: uuid.UUID
    service_name: str
    team_id: Optional[uuid.UUID]
    team_name: Optional[str]
    applies_when: Optional[dict[str, Any]] = None


class ChainWhereUsedResponse(BaseModel):
    chain_id: uuid.UUID
    items: list[ChainWhereUsedItem]
    total: int


class EscalationCalendarLevel(BaseModel):
    level: int
    target_type: str
    target_id: uuid.UUID
    target_name: str
    resolved_user_id: Optional[uuid.UUID] = None
    resolved_user_name: Optional[str] = None
    resolved_user_email: Optional[str] = None
    coverage_start: Optional[str] = None
    coverage_end: Optional[str] = None
    status: str
    warnings: list[str] = Field(default_factory=list)


class EscalationCalendarDay(BaseModel):
    date: date
    levels: list[EscalationCalendarLevel]


class EscalationCalendarResponse(BaseModel):
    chain_id: uuid.UUID
    chain_name: str
    team_id: uuid.UUID
    team_name: Optional[str] = None
    start: date
    end: date
    range: str
    days: list[EscalationCalendarDay]


class IncidentPageResponse(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    user_id: uuid.UUID
    chain_id: Optional[uuid.UUID]
    step_index: Optional[int]
    channel: str
    sent_at: datetime
    ack_at: Optional[datetime]
    ack_via: Optional[str]
    delivery_status: str

    model_config = {"from_attributes": True}


class IncidentChainStateResponse(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    chain_id: uuid.UUID
    status: str
    current_step_index: int
    next_step_due_at: Optional[datetime]
    hard_deadline_at: Optional[datetime]
    pending_takeover_user_id: Optional[uuid.UUID]
    pending_takeover_expires_at: Optional[datetime]
    started_at: datetime
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


class IncidentChainPanelResponse(BaseModel):
    incident_id: uuid.UUID
    state: Optional[IncidentChainStateResponse]
    pages: list[IncidentPageResponse]
    # AI auto-start outcome triggered by an acknowledgment (T1/T2 start here;
    # T0 already started at incident creation). Optional so other callers of the
    # chain panel are unaffected.
    auto_start_status: Optional[str] = None
    auto_start_reason: Optional[str] = None
    resolved_tier: Optional[int] = None
    auto_start_message: Optional[str] = None


class IncidentAckRequest(BaseModel):
    via: str = Field(default="web_ui", pattern="^(button_click|slash_command|web_ui|api)$")


class IncidentTakeRequest(BaseModel):
    confirm: bool = False  # true = confirm a pending soft-takeover
    force: bool = False  # true = admin force-takeover


# ---------------------------------------------------------------------------
# AI incident memory (Sprint 45 Step 6)
# ---------------------------------------------------------------------------


class IncidentMemoryResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    service_id: Optional[uuid.UUID] = None
    source_incident_id: Optional[uuid.UUID] = None
    title: str
    summary_md: str
    tags: list[str] = Field(default_factory=list)
    helpful_count: int = 0
    unhelpful_count: int = 0
    can_edit: bool = False
    can_delete: bool = False
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class IncidentMemoryListResponse(BaseModel):
    items: list[IncidentMemoryResponse]
    total: int


class IncidentMemoryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    summary_md: str = Field(..., min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=5)
    service_id: Optional[uuid.UUID] = None


class IncidentMemoryUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    summary_md: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    tags: Optional[list[str]] = Field(default=None, max_length=5)
    # Use a sentinel so an operator can clear the service binding (set to null)
    # vs leave it untouched (field omitted from request body).
    service_id: Optional[uuid.UUID] = None
    service_id_set: bool = False


class IncidentMemoryFeedbackRequest(BaseModel):
    helpful: bool


class IncidentMemoryBulkDeleteRequest(BaseModel):
    memory_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


class IncidentMemoryBulkDeleteResponse(BaseModel):
    deleted: int


class SessionMemoriesUsedItem(BaseModel):
    memory: IncidentMemoryResponse
    surfaced_at: datetime
    score: Optional[float] = None


class SessionMemoriesUsedResponse(BaseModel):
    items: list[SessionMemoriesUsedItem]
    total: int


# ---------------------------------------------------------------------------
# Data retention (Sprint 53)
# ---------------------------------------------------------------------------


class RetentionCategoryConfig(BaseModel):
    category: str
    ttl_days: Optional[int] = Field(
        default=None,
        description="NULL disables auto-deletion for this category.",
    )
    last_pruned_at: Optional[datetime] = None
    last_pruned_count: Optional[int] = None
    is_default: bool = Field(
        default=False,
        description="True when the operator hasn't overridden the system default.",
    )


class RetentionCategoryStorage(BaseModel):
    category: str
    row_count: int
    estimated_bytes: int
    avg_bytes_per_row: int
    non_prunable: bool = False


class RetentionStatusResponse(BaseModel):
    default_ttl_days: int
    scheduler_enabled: bool
    last_run_at: Optional[datetime] = None
    configs: list[RetentionCategoryConfig]
    storage: list[RetentionCategoryStorage]


class RetentionUpdateItem(BaseModel):
    category: str
    ttl_days: Optional[int] = Field(
        default=None,
        description="NULL disables auto-deletion. Otherwise must be >= 1.",
    )


class RetentionUpdateRequest(BaseModel):
    configs: list[RetentionUpdateItem] = Field(..., min_length=1, max_length=20)


class RetentionRunReportItem(BaseModel):
    category: str
    ttl_days: Optional[int]
    cutoff: Optional[datetime]
    deleted_count: int
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


class RetentionRunReportResponse(BaseModel):
    started_at: datetime
    finished_at: Optional[datetime]
    total_deleted: int
    total_errors: int
    items: list[RetentionRunReportItem]
