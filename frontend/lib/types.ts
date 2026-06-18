// TypeScript types matching backend Pydantic schemas exactly.

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  username: string;
  email: string;
  auth_source: string;
  role: "admin" | "operator" | "viewer";
  is_active: boolean;
  first_name?: string | null;
  last_name?: string | null;
  avatar_color?: string | null;
  must_change_password?: boolean;
  primary_org_id: string | null;
  created_at: string;
  // Sprint 56: soft-delete marker. When set, the user is hidden from
  // active lists; per-user pages render the deleted-state placeholder.
  deleted_at?: string | null;
}

export interface UserListResponse {
  items: UserResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Incidents
// ---------------------------------------------------------------------------

export type Severity = "critical" | "high" | "medium" | "low";
export type IncidentStatus = "open" | "in_progress" | "resolved" | "closed" | "merged";

export interface IncidentResponse {
  id: string;
  title: string;
  description: string;
  status: IncidentStatus;
  severity: Severity | null;
  service_id: string | null;
  ingestion_model_config_id?: string | null;
  service_name?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  external_id: string | null;
  external_source: string | null;
  merged_into_incident_id?: string | null;
  created_at: string;
  updated_at: string;
  // First-acknowledgment timestamp (MTTA). Null until taken/acked.
  acknowledged_at?: string | null;
  // Responder / assignment state (Part 6)
  responder_user_id?: string | null;
  responder_display_name?: string | null;
  responder_email?: string | null;
  responder_state?: "awaiting" | "assigned" | "escalated" | "unassigned";
  acknowledged_by_user_id?: string | null;
  acknowledged_by_display_name?: string | null;
  escalated_to_user_id?: string | null;
  escalated_to_display_name?: string | null;
  // AI session state — computed by the list/detail route so the table can show
  // an "AI in progress" indicator without a per-incident fetch.
  ai_session_active?: boolean;
  ai_session_status?: SessionStatus | null;
}

export interface IncidentListResponse {
  items: IncidentResponse[];
  total: number;
}

export interface IncidentCombineResponse {
  primary: IncidentResponse;
  merged_incident_ids: string[];
  moved_comments: number;
  stopped_sessions: number;
}

export interface IncidentCreate {
  title: string;
  description: string;
  severity?: Severity;
  service_id?: string;
  external_id?: string;
  external_source?: string;
}

export interface FireTestIncidentRequest {
  service_id?: string;
}

export interface FireTestIncidentResponse {
  incident: IncidentResponse;
  resolved_tier: number;
  auto_start_status: "queued" | "skipped" | "failed";
  auto_start_reason: string | null;
  message: string;
}

export interface IncidentCreateResponse extends IncidentResponse {
  resolved_tier: number;
  auto_start_status: "queued" | "skipped" | "failed";
  auto_start_reason: string | null;
  auto_start_message: string;
}

export interface IncidentUpdate {
  status?: IncidentStatus;
  severity?: Severity;
  service_id?: string | null;
  service_id_set?: boolean;
  handoff_reason?: string;
}

export interface IncidentTimelineItemResponse {
  id: string;
  happened_at: string;
  lane: "response" | "tool" | "evidence" | string;
  event_type: string;
  title: string;
  body: string | null;
  actor_user_id: string | null;
  actor_label: string | null;
  status: string | null;
  session_id: string | null;
  session_label: string | null;
  session_tier: number | null;
  tool_name: string | null;
  safety_class: string | null;
  tier_decision: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown> | null;
  json_payload: Record<string, unknown> | null;
}

export interface IncidentTimelineResponse {
  items: IncidentTimelineItemResponse[];
  total: number;
}

// Sprint 61 Step 4 — postmortem authoring.
export interface IncidentPostmortemResponse {
  incident_id: string;
  postmortem_md: string | null;
  postmortem_updated_at: string | null;
  template: string;
}

export interface IncidentPostmortemUpdate {
  postmortem_md: string | null;
}

export interface IncidentCommentResponse {
  id: string;
  incident_id: string;
  body: string;
  author_user_id: string | null;
  author_label: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentCommentListResponse {
  items: IncidentCommentResponse[];
  total: number;
}

export interface PostmortemMemoryCandidate {
  memory_id: string | null;
  title: string;
  created: boolean;
}

export interface PostmortemMemoryCandidatesResponse {
  created: number;
  skipped: number;
  items: PostmortemMemoryCandidate[];
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export type SessionStatus =
  | "active"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "timed_out"
  | "stopped";

export interface SessionResponse {
  id: string;
  incident_id: string | null;
  workflow_profile_id: string | null;
  agent_team_profile_id: string | null;
  tier: number;
  model_provider: string | null;
  model_id: string | null;
  status: SessionStatus;
  summary: string | null;
  started_at: string;
  ended_at: string | null;
  tier0_max_session_seconds: number | null;
}

export interface SessionListResponse {
  items: SessionResponse[];
  total: number;
}

export interface SessionCreate {
  incident_id?: string;
  workflow_profile_id?: string;
  agent_team_profile_id?: string;
  tier?: number;
  model_provider?: string;
  model_id?: string;
  initial_briefing?: string;
}

export interface SessionOverrideRequest {
  // Target tier for an intercept-override: 1 (Approval Required) or 2 (Advisory).
  tier: number;
}

// ---------------------------------------------------------------------------
// Session messages (co-pilot chat)
// ---------------------------------------------------------------------------

export type SessionMessageRole = "user" | "assistant";

export interface SessionMessageResponse {
  id: string;
  session_id: string;
  role: SessionMessageRole;
  content: string;
  created_at: string;
  consumed_by_workflow: boolean;
  node_context: string | null;
}

export interface SessionMessageListResponse {
  items: SessionMessageResponse[];
  total: number;
}

export interface SessionMessageCreate {
  content: string;
}

export interface SessionRollbackRequest {
  mcp_server?: string;
  dry_run?: boolean;
}

export interface RollbackStepResponse {
  original_tool: string;
  inverse_tool: string | null;
  parameters: Record<string, unknown>;
  status: string;
  error: string | null;
}

export interface SessionRollbackResponse {
  session_id: string;
  dry_run: boolean;
  attempted: number;
  succeeded: number;
  failed: number;
  skipped: number;
  steps: RollbackStepResponse[];
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditEntryResponse {
  id: string;
  session_id: string;
  timestamp: string;
  tier: number;
  entry_type: string;
  tool_name: string | null;
  tool_parameters: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  permitted: boolean;
  block_reason: string | null;
  duration_ms: number | null;
}

export interface AuditListResponse {
  items: AuditEntryResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "redirected"
  | "expired";

export interface ApprovalRequestResponse {
  id: string;
  session_id: string;
  action: Record<string, unknown>;
  justification: string | null;
  status: ApprovalStatus;
  resolution_note: string | null;
  requested_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  expires_at: string;
}

export interface ApprovalListResponse {
  items: ApprovalRequestResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// Sprint 56 — admin People-surface request/response types
export interface UserUpdateRequest {
  role?: "admin" | "operator" | "viewer";
  is_active?: boolean;
}

export interface UserCreateRequest {
  username: string;
  email: string;
  role: "admin" | "operator" | "viewer";
  password: string;
  is_active?: boolean;
  first_name?: string | null;
  last_name?: string | null;
}

export interface MeUpdateRequest {
  username?: string;
  email?: string;
  first_name?: string | null;
  last_name?: string | null;
  avatar_color?: string | null;
}

export interface MePasswordChangeRequest {
  current_password: string;
  new_password: string;
}

export interface PasswordResetMintResponse {
  url: string;
  expires_at: string;
  email_sent: boolean;
  email_error: string | null;
}

export interface SoftDeletePreconditions {
  is_active: boolean;
  roster_memberships: number;
  can_delete: boolean;
}

// --- Invites (Sprint 56 Step 4) ---
export type InviteStatus = "pending" | "accepted" | "expired" | "revoked";

export interface InviteResponse {
  id: string;
  org_id: string;
  email: string;
  role: "admin" | "operator" | "viewer";
  invited_by_user_id: string | null;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  status: InviteStatus;
}

export interface InviteListResponse {
  items: InviteResponse[];
  total: number;
}

export interface InviteCreateRequest {
  email: string;
  role: "admin" | "operator" | "viewer";
  first_name?: string | null;
  last_name?: string | null;
}

export interface InviteCreatedResponse {
  invite: InviteResponse;
  url: string;
  email_sent: boolean;
  email_error: string | null;
}

export interface InvitePublicResponse {
  email: string;
  role: "admin" | "operator" | "viewer";
  org_name: string;
  expires_at: string;
  first_name?: string | null;
  last_name?: string | null;
}

export interface InviteAcceptRequest {
  username: string;
  password: string;
  first_name?: string | null;
  last_name?: string | null;
}

export interface ConfigResponse {
  tier: number;
  mcp_servers: Record<string, unknown>[];
  audit_output: string;
  logging_level: string;
  // Sprint 56: deployment-level flags the People surface reads to decide
  // whether to show the multi-org affordances and whether to advertise
  // SMTP delivery alongside the copy-paste invite URLs.
  multi_org_enabled?: boolean;
  smtp_configured?: boolean;
  // Sprint 64: simple-by-default auth visibility flags.
  // `advanced_auth_enabled` is env-driven; the two `*_configured`
  // booleans are per-tenant DB lookups. Frontend rule for surfacing
  // SSO/SAML admin settings:
  //   advanced_auth_enabled || sso_configured || saml_configured.
  advanced_auth_enabled?: boolean;
  sso_configured?: boolean;
  saml_configured?: boolean;
  // v1 paging: absolute base URL used to render a service's full alert
  // intake URL. Null/undefined when unset; the browser then falls back to
  // window.location.origin.
  public_base_url?: string | null;
}

export interface ConfigUpdate {
  tier?: number;
  logging_level?: string;
}

export interface SetupChecklistResponse {
  model_configured: boolean;
  mcp_server_added: boolean;
  skill_defined: boolean;
  ingest_token_created: boolean;
  paging_service_added: boolean;
  all_complete: boolean;
}

// ---------------------------------------------------------------------------
// Models / Providers
// ---------------------------------------------------------------------------

export interface ModelConfigResponse {
  id: string;
  name: string;
  provider: string;
  model_id: string;
  api_key_env_var: string | null;
  base_url: string | null;
  api_version: string | null;
  provider_meta: Record<string, string> | null;
  max_tokens: number;
  temperature: number;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
}

export interface ModelConfigListResponse {
  items: ModelConfigResponse[];
  total: number;
}

export interface ModelConfigValidationIssue {
  code: string;
  message: string;
}

export interface ModelConfigSaveResponse {
  config: ModelConfigResponse;
  warnings: ModelConfigValidationIssue[];
}

export interface ModelConfigTestResponse {
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
  error: string | null;
}

export interface ModelBootstrapStatusResponse {
  needs_setup: boolean;
  has_configs: boolean;
  has_default: boolean;
  default_config: ModelConfigResponse | null;
}

export interface ModelConfigUpdate {
  name?: string;
  provider: string;
  model_id: string;
  api_key_env_var?: string;
  base_url?: string;
  api_version?: string;
  provider_meta?: Record<string, string>;
  max_tokens?: number;
  temperature?: number;
}

export interface ProviderModelsResponse {
  provider: string;
  label: string;
  default_model_id: string;
  default_api_key_env_var: string | null;
  requires_api_key: boolean;
  requires_base_url: boolean;
  requires_api_version: boolean;
  available: boolean;
  models: string[];
  error: string | null;
}

export interface ProviderModelsListResponse {
  items: ProviderModelsResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// MCP Servers
// ---------------------------------------------------------------------------

export type MCPTransport = "stdio" | "sse" | "http";

export interface MCPServerResponse {
  id: string;
  name: string;
  transport: MCPTransport;
  command: string | null;
  args: string[] | null;
  url: string | null;
  env_vars: Record<string, string> | null;
  is_active: boolean;
  created_at: string;
  has_token: boolean;
  /** "connected" | "reconnect_needed" | null */
  oauth_status: string | null;
}

export interface MCPServerListResponse {
  items: MCPServerResponse[];
  total: number;
}

export type MCPServerConnectionStatus = "healthy" | "stale" | "error";

export interface MCPServerStatusResponse {
  server_id: string;
  status: MCPServerConnectionStatus;
  last_successful_call_at: string | null;
  last_error: string | null;
}

export interface MCPServerStatusListResponse {
  items: MCPServerStatusResponse[];
  total: number;
}

export interface MCPServerUpsert {
  name: string;
  transport: MCPTransport;
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  token?: string | null;
  clear_token?: boolean;
  env_vars?: Record<string, string> | null;
  is_active?: boolean;
}

export interface MCPServerTestResponse {
  success: boolean;
  detail: string;
  tool_count: number;
  tool_names: string[];
}

export interface MCPOAuthStartResponse {
  authorize_url: string;
}

// ---------------------------------------------------------------------------
// Legacy chat connector API
// ---------------------------------------------------------------------------

export type BotConnectorPlatform =
  | "telegram"
  | "signal"
  | "whatsapp"
  | "slack"
  | "discord"
  | "teams"
  | "mattermost"
  | "matrix"
  | "feishu"
  | "dingtalk"
  | "wecom"
  | "weixin"
  | "twilio"
  | "email"
  | "smtp"
  | "homeassistant"
  | "bluebubbles"
  | "custom";

export type BotConnectorCapability =
  | "incident_lookup"
  | "session_status"
  | "approvals"
  | "copilot_chat"
  | "notifications";

export type BotConnectorStatus =
  | "not_configured"
  | "configured"
  | "healthy"
  | "error"
  | "disabled";

export type NotificationTeamScope = "workspace" | "teams";

// Honest per-platform capability descriptor (see backend bots/capabilities.py).
// Drives what the Notification Channels UI advertises — it never offers an
// action a platform cannot securely support.
export interface PlatformCapabilities {
  platform: string;
  display_name: string;
  delivery: boolean;
  incident_card: boolean;
  incident_updates: boolean;
  interactive_actions: boolean;
  direct_message: boolean;
  shared_channel: boolean;
  ai_session_link: boolean;
  message_update: boolean;
  delivery_only: boolean;
}

export interface BotConnectorResponse {
  id: string;
  name: string;
  platform: BotConnectorPlatform;
  config: Record<string, unknown> | null;
  allowed_capabilities: BotConnectorCapability[];
  status: BotConnectorStatus;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
  last_checked_at: string | null;
  last_error: string | null;
  credential_keys: string[];
  has_credentials: boolean;
  team_scope: NotificationTeamScope;
  team_ids: string[];
  team_names: string[];
  native_actions_enabled?: boolean;
  callback_status?: string;
  callback_last_verified_at?: string | null;
  callback_last_error?: string | null;
  platform_label: string | null;
  platform_capabilities: PlatformCapabilities | null;
}

export interface BotConnectorListResponse {
  items: BotConnectorResponse[];
  total: number;
}

export interface BotConnectorUpsert {
  name: string;
  platform: BotConnectorPlatform;
  config?: Record<string, unknown> | null;
  credentials?: Record<string, string> | null;
  clear_credentials?: boolean;
  allowed_capabilities: BotConnectorCapability[];
  team_scope?: NotificationTeamScope;
  team_ids?: string[];
  status?: BotConnectorStatus;
  is_enabled?: boolean;
  native_actions_enabled?: boolean;
}

export type BotConnectorTestCheckLevel = "pass" | "warn" | "fail";

export interface BotConnectorTestCheck {
  name: string;
  level: BotConnectorTestCheckLevel;
  detail: string;
}

export interface BotConnectorTestResponse {
  success: boolean;
  detail: string;
  status: BotConnectorStatus;
  checks: BotConnectorTestCheck[];
  live_message_sent: boolean;
  target_chat_id: string | null;
}

export interface BotConnectorTestRequest {
  live?: boolean;
  chat_id?: string;
}

export type BotConnectorFieldKind =
  | "text"
  | "secret"
  | "select"
  | "textarea"
  | "url";

export type BotConnectorFieldGroup = "config" | "credentials";

export interface BotConnectorFieldOption {
  value: string;
  label: string;
}

export interface BotConnectorFieldSchema {
  name: string;
  label: string;
  kind: BotConnectorFieldKind;
  group: BotConnectorFieldGroup;
  required: boolean;
  default: unknown | null;
  helper: string | null;
  doc_url: string | null;
  placeholder: string | null;
  options: BotConnectorFieldOption[];
}

export interface BotConnectorPlatformSchema {
  platform: string;
  fields: BotConnectorFieldSchema[];
  oauth_enabled: boolean;
  label: string | null;
  capabilities: PlatformCapabilities | null;
}

export interface BotConnectorPlatformListResponse {
  items: BotConnectorPlatformSchema[];
  total: number;
}

// ---------------------------------------------------------------------------
// Legacy viewer-update webhook API
// ---------------------------------------------------------------------------

export type WebhookTriggerEventType =
  | "*"
  | "session.created"
  | "session.awaiting_approval"
  | "session.active"
  | "session.completed"
  | "session.failed"
  | "session.timed_out";

export type WebhookTriggerFormat = "generic" | "slack" | "teams" | "sumo";

export interface WebhookTriggerResponse {
  id: string;
  name: string;
  url: string;
  format: WebhookTriggerFormat;
  event_types: WebhookTriggerEventType[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_triggered_at: string | null;
  last_error: string | null;
  header_names: string[];
  has_token: boolean;
}

export interface WebhookTriggerListResponse {
  items: WebhookTriggerResponse[];
  total: number;
}

export interface WebhookTriggerUpsert {
  name: string;
  url: string;
  format: WebhookTriggerFormat;
  event_types: WebhookTriggerEventType[];
  headers?: Record<string, string> | null;
  clear_headers?: boolean;
  token?: string | null;
  clear_token?: boolean;
  is_active?: boolean;
}

export interface WebhookTriggerTestResponse {
  success: boolean;
  detail: string;
  status_code: number | null;
  event_type: string;
}

// ---------------------------------------------------------------------------
// Workflow Profiles
// ---------------------------------------------------------------------------

export type WorkflowNode =
  | "observe"
  | "diagnose"
  | "plan"
  | "tier_gate"
  | "execute"
  | "verify"
  | "summarize";

export type AgentRole =
  | "incident_commander"
  | "investigator"
  | "skeptic"
  | "remediator";

export interface WorkflowProfileResponse {
  id: string;
  name: string;
  description: string | null;
  node_order: WorkflowNode[];
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkflowProfileListResponse {
  items: WorkflowProfileResponse[];
  total: number;
}

export interface SessionProfileTemplate {
  key: string;
  name: string;
  description: string;
  node_order: WorkflowNode[];
}

export interface SessionProfileTemplateListResponse {
  items: SessionProfileTemplate[];
  total: number;
}

export interface WorkflowProfileUpsert {
  name: string;
  description?: string | null;
  node_order: WorkflowNode[];
  is_active?: boolean;
  is_default?: boolean;
}

export interface AgentTeamProfileResponse {
  id: string;
  name: string;
  description: string | null;
  roles: AgentRole[];
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentTeamProfileListResponse {
  items: AgentTeamProfileResponse[];
  total: number;
}

export interface AgentTeamProfileUpsert {
  name: string;
  description?: string | null;
  roles: AgentRole[];
  is_active?: boolean;
  is_default?: boolean;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export type SkillAssignment = "unassigned" | "global" | "server";

export interface SkillResponse {
  id: string;
  name: string;
  description: string | null;
  mcp_server_id: string | null;
  assignment: SkillAssignment;
  content_md: string;
  focus_areas: string[];
  created_at: string;
  updated_at: string;
}

export interface SkillListResponse {
  items: SkillResponse[];
  total: number;
}

export interface SkillCreate {
  name: string;
  content_md: string;
  description?: string | null;
  mcp_server_id?: string | null;
  assignment?: SkillAssignment;
}

export interface SkillUpdate {
  name: string;
  content_md: string;
  description?: string | null;
  mcp_server_id?: string | null;
  assignment?: SkillAssignment;
}

export interface SkillTemplateResponse {
  name: string;
  content_md: string;
}

export interface SkillCloneRequest {
  name: string;
  mcp_server_id?: string | null;
  description?: string | null;
}

export type SkillClassification = "safe" | "caution" | "destructive";

export interface SkillDiscoveredTool {
  name: string;
  description: string | null;
  suggested_classification: SkillClassification;
  generic: boolean;
  suggested_deny: boolean;
  needs_review: boolean;
  rationale: string;
}

export interface SkillDiscoverResponse {
  mcp_server_id: string;
  mcp_server_name: string;
  tools: SkillDiscoveredTool[];
}

export interface SkillGenerateOperation {
  tool: string;
  classification: SkillClassification;
  deny?: boolean;
  allow_generic?: boolean;
  reversible?: boolean | null;
  notes?: string | null;
}

export interface SkillGenerateRequest {
  name: string;
  description?: string | null;
  environment?: string;
  operations: SkillGenerateOperation[];
  tier0_instructions?: string;
  tier1_instructions?: string;
  tier2_instructions?: string;
}

export interface SkillGenerateResponse {
  name: string;
  content_md: string;
}

export interface SkillAISuggestRequest {
  intent?: string;
  environment?: string;
  tools: { name: string; description?: string | null }[];
}

export interface SkillAISuggestedTool {
  name: string;
  classification: SkillClassification;
  deny: boolean;
  allow_generic: boolean;
  reversible: boolean | null;
  compensating_inverse: string | null;
  generic: boolean;
  needs_review: boolean;
  rationale: string;
}

export interface SkillAISuggestResponse {
  tools: SkillAISuggestedTool[];
  tier0_instructions: string;
  tier1_instructions: string;
  tier2_instructions: string;
  environment: string;
}

// ---------------------------------------------------------------------------
// Legacy intake token API
// ---------------------------------------------------------------------------

export type IngestProvider =
  | "auto"
  | "cloudwatch"
  | "azure_monitor"
  | "gcp_monitoring"
  | "oci_monitoring"
  | "generic";

export interface IngestTokenResponse {
  id: string;
  name: string;
  provider: IngestProvider;
  is_active: boolean;
  created_at: string;
  last_used_at: string | null;
  shape_cache_size: number;
}

export interface IngestTokenCreatedResponse {
  id: string;
  name: string;
  provider: IngestProvider;
  token: string;
  is_active: boolean;
  created_at: string;
}

export interface IngestTokenListResponse {
  items: IngestTokenResponse[];
  total: number;
}

export interface IngestTokenCreate {
  name: string;
  provider: IngestProvider;
  sample_payload?: Record<string, unknown> | null;
}

export interface IngestProviderItem {
  key: string;
  label: string;
}

export interface IngestProviderListResponse {
  items: IngestProviderItem[];
}

export interface IngestLearnPreview {
  title: string;
  description: string;
  severity: string | null;
  external_id: string | null;
  status: string;
}

export interface IngestTokenLearnShapeResponse {
  shape_hash: string;
  paths: Record<string, string>;
  cache_hit: boolean;
  preview: IngestLearnPreview;
}

// ---------------------------------------------------------------------------
// WebSocket messages
// ---------------------------------------------------------------------------

export type WSMessageType =
  | "node_transition"
  | "tool_call"
  | "approval_requested"
  | "approval_resolved"
  | "chat_message_user"
  | "chat_message_assistant"
  | "error"
  | "session_end"
  | "session_overridden";

export interface WSMessage {
  type: WSMessageType;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Reliability (Sprint 25)
// ---------------------------------------------------------------------------

export type SLATargetKind = "http" | "tcp" | "external";

export type UptimeStatus = "up" | "down" | "unknown";

export interface SLATargetResponse {
  id: string;
  name: string;
  kind: SLATargetKind;
  config: Record<string, unknown> | null;
  owner_team: string | null;
  service_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  // Computed convenience fields (Reliability v1)
  url: string | null;
  monitor_type: string | null; // "http" | "https"
  current_status: UptimeStatus;
  last_check_at: string | null;
  uptime_30d_pct: number | null;
  active_slo_count: number;
  // Resolved from service_id (Phase 6)
  service_name: string | null;
  team_id: string | null;
  team_name: string | null;
}

export interface SLORecommendation {
  slo_id: string;
  slo_name: string;
  target_id: string;
  target_name: string;
  severity: "critical" | "warning";
  objective_pct: number;
  actual_pct: number;
  error_budget_remaining_pct: number;
  burn_rate: number;
  target_status: UptimeStatus;
  service_id: string | null;
  service_name: string | null;
  team_id: string | null;
  team_name: string | null;
  headline: string;
  actions: string[];
}

export interface SLORecommendationsResponse {
  items: SLORecommendation[];
  total: number;
  generated_at: string;
}

export interface SLASummaryResponse {
  total_targets: number;
  targets_up: number;
  targets_down: number;
  targets_unknown: number;
  avg_uptime_30d_pct: number | null;
  active_slo_warnings: number;
}

export interface SLATargetListResponse {
  items: SLATargetResponse[];
  total: number;
}

export interface SLATargetCreate {
  name: string;
  kind: SLATargetKind;
  config?: Record<string, unknown> | null;
  owner_team?: string | null;
  is_active?: boolean;
}

export interface SLATargetUpdate {
  name?: string;
  kind?: SLATargetKind;
  config?: Record<string, unknown> | null;
  owner_team?: string | null;
  is_active?: boolean;
}

export interface SLOResponse {
  id: string;
  target_id: string;
  name: string;
  objective_pct: number;
  window_seconds: number;
  burn_alert_threshold: number | null;
  is_active: boolean;
  created_at: string;
}

export interface SLOListResponse {
  items: SLOResponse[];
  total: number;
}

export type MaintenanceWindowScopeType = "global" | "service" | "roster" | "team";

export type NotificationChannelKey = "slack_dm" | "teams_dm" | "email" | "sms";

/**
 * One ordered notification escalation stage for a priority. `channel_id` is a
 * configured Notification Channel id (BotConnector) or a legacy delivery key.
 * `delay_seconds` gates the *next* stage if the incident stays unacknowledged.
 */
export interface RoutingStage {
  channel_id: string;
  delay_seconds: number;
}

export interface QuietHoursWindow {
  start: string;
  end: string;
}

export interface QuietHoursConfig {
  // Flat window shape read by the dispatcher (backend/paging/dispatch.py).
  weekday_start?: string | null;
  weekday_end?: string | null;
  // Optional days-of-week restriction; Python weekday ints (Mon=0 .. Sun=6).
  days?: number[] | null;
  min_priority_to_break?: Priority | null;
  time_zone?: string | null;
  // Legacy nested shape kept for backward-compatible reads.
  weekday?: QuietHoursWindow | null;
  weekend?: QuietHoursWindow | null;
}

export interface UserNotificationPrefResponse {
  user_id: string;
  org_id: string;
  channels: Record<string, Record<string, string>>;
  // New shape: ordered stages per priority. Legacy rows may still be a flat
  // list of channel-key strings; the UI normalizes both on load.
  routing: Record<string, (RoutingStage | string)[]>;
  quiet_hours: QuietHoursConfig | null;
  updated_at: string;
}

export interface NotificationSettingsResponse {
  org_id: string;
  notification_dedup_window_minutes: number;
}

export interface MaintenanceWindowResponse {
  id: string;
  name: string;
  reason: string | null;
  description: string | null;
  starts_at: string;
  ends_at: string;
  rrule: string | null;
  target_ids: string[];
  scope_type: MaintenanceWindowScopeType;
  scope_id: string | null;
  scope_ids: string[];
  created_by: string | null;
  created_at: string;
  approved: boolean;
  approved_by: string | null;
  approved_at: string | null;
}

export interface MaintenanceWindowListResponse {
  items: MaintenanceWindowResponse[];
  total: number;
}

export interface UptimeSeriesPoint {
  ts: string;
  up_pct: number;
  status: UptimeStatus;
}

export interface UptimeEpisode {
  started_at: string;
  ended_at: string | null; // null == ongoing
  duration_seconds: number;
  maintenance: boolean;
}

export interface SLATargetUptimeResponse {
  uptime_pct: number;
  total_samples: number;
  up_samples: number;
  downtime_seconds: number;
  suppressed_seconds: number;
  mtbf_seconds: number | null;
  down_events: number;
  series: UptimeSeriesPoint[];
  episodes: UptimeEpisode[];
}

export interface ResponseTimeSeriesPoint {
  ts: string;
  avg_latency_ms: number | null;
  min_latency_ms: number | null;
  max_latency_ms: number | null;
  samples: number;
}

export interface ResponseTimeResponse {
  target_id: string;
  window: string;
  avg_latency_ms: number | null;
  min_latency_ms: number | null;
  max_latency_ms: number | null;
  total_samples: number;
  series: ResponseTimeSeriesPoint[];
}

export interface SLOStatusResponse {
  objective_pct: number;
  actual_pct: number;
  error_budget_remaining_pct: number;
  burn_rate: number;
  compliant: boolean;
}
// ---------------------------------------------------------------------------
// Bot User Links (Sprint 27)
// ---------------------------------------------------------------------------

export interface BotUserLinkResponse {
  id: string;
  connector_id: string;
  platform_user_id: string;
  external_username?: string | null;
  external_display_name?: string | null;
  opsmender_user_id: string;
  opsmender_username: string;
  opsmender_role: string;
  created_at: string;
  last_seen_at?: string | null;
  verified?: boolean;
}

export interface BotUserLinkListResponse {
  items: BotUserLinkResponse[];
  total: number;
}

export interface BotUserLinkCreate {
  platform_user_id: string;
  external_username?: string | null;
  external_display_name?: string | null;
  opsmender_user_id: string;
}


// ---------------------------------------------------------------------------
// Organizations (Phase 4)
// ---------------------------------------------------------------------------


export interface BrandingConfig {
  company_name?: string;
  logo_url?: string;
  primary_color?: string; // Hex code
  secondary_color?: string; // Hex code
  favicon_url?: string;
}


export interface OrganizationResponse {
  id: string;
  name: string;
  slug: string;
  branding: BrandingConfig | null;
  created_at: string;
}


export interface OrganizationListResponse {
  items: OrganizationResponse[];
  total: number;
}


export interface OrganizationCreate {
  name: string;
  slug?: string;
  branding?: BrandingConfig;
}


export interface OrganizationUpdate {
  name?: string;
  slug?: string;
  branding?: BrandingConfig;
}


export interface UserOrganizationResponse {
  user_id: string;
  username: string;
  email: string;
  role: "admin" | "operator" | "viewer";
  joined_at: string;
}


export interface OrganizationUserListResponse {
  items: UserOrganizationResponse[];
  total: number;
}


export interface UserOrganizationLink {
  user_id: string;
  role: "admin" | "operator" | "viewer";
}


export interface MyOrganizationResponse {
  id: string;
  name: string;
  slug: string;
  branding?: BrandingConfig | null;
  role: "admin" | "operator" | "viewer";
  is_primary: boolean;
}


export interface MyOrganizationListResponse {
  items: MyOrganizationResponse[];
  total: number;
}


export interface OrganizationDomainResponse {
  id: string;
  org_id: string;
  domain: string;
  is_primary: boolean;
  verified: boolean;
  created_at: string;
}


export interface OrganizationDomainListResponse {
  items: OrganizationDomainResponse[];
  total: number;
}


export interface OrganizationDomainCreate {
  domain: string;
  is_primary?: boolean;
  verified?: boolean;
}


export interface TenantContextResponse {
  pinned: boolean;
  org_id?: string | null;
  org_name?: string | null;
  org_slug?: string | null;
  branding?: BrandingConfig | null;
  host?: string | null;
  sso_enabled?: boolean;
  sso_login_path?: string | null;
  saml_enabled?: boolean;
  saml_login_path?: string | null;
}


export interface OrgSSOConfigResponse {
  id: string;
  org_id: string;
  provider: "oidc";
  is_active: boolean;
  discovery_url: string;
  client_id: string;
  has_client_secret: boolean;
  scopes: string;
  email_claim: string;
  name_claim: string;
  default_role: "admin" | "operator" | "viewer";
  allowed_email_domains: string | null;
  created_at: string;
  updated_at: string;
}


export interface OrgSSOConfigCreate {
  provider?: "oidc";
  discovery_url: string;
  client_id: string;
  client_secret?: string | null;
  is_active?: boolean;
  scopes?: string;
  email_claim?: string;
  name_claim?: string;
  default_role?: "admin" | "operator" | "viewer";
  allowed_email_domains?: string | null;
}


export interface OrgSAMLConfigResponse {
  id: string;
  org_id: string;
  is_active: boolean;
  idp_metadata_url: string | null;
  has_idp_metadata_xml: boolean;
  email_attribute: string;
  name_attribute: string;
  default_role: "admin" | "operator" | "viewer";
  allowed_email_domains: string | null;
  want_assertions_signed: boolean;
  want_response_signed: boolean;
  created_at: string;
  updated_at: string;
}


export interface OrgSAMLConfigCreate {
  is_active?: boolean;
  idp_metadata_url?: string | null;
  idp_metadata_xml?: string | null;
  email_attribute?: string;
  name_attribute?: string;
  default_role?: "admin" | "operator" | "viewer";
  allowed_email_domains?: string | null;
  want_assertions_signed?: boolean;
  want_response_signed?: boolean;
}

// ---------------------------------------------------------------------------
// Auditor (Sprint 32)
// ---------------------------------------------------------------------------

export type AuditSeverity = "critical" | "high" | "medium" | "low" | "info";
export type AuditRunStatus = "queued" | "running" | "completed" | "failed";
export type AuditFindingStatus = "open" | "remediating" | "resolved" | "dismissed";

export interface AuditAnalyzerResponse {
  key: string;
  label: string;
  description: string;
}

export interface AuditAnalyzerListResponse {
  items: AuditAnalyzerResponse[];
  total: number;
}

export interface AuditRunCreate {
  analyzers: string[];
  analyzer_params?: Record<string, Record<string, unknown>>;
  execute?: boolean;
}

export interface AuditRunResponse {
  id: string;
  status: AuditRunStatus;
  analyzers: string[];
  started_at: string | null;
  finished_at: string | null;
  finding_count: number;
  error: string | null;
  created_by: string | null;
  created_at: string;
}

export interface AuditRunListResponse {
  items: AuditRunResponse[];
  total: number;
}

export interface AuditFindingResponse {
  id: string;
  run_id: string;
  analyzer: string;
  severity: AuditSeverity;
  category: string | null;
  resource: string | null;
  message: string;
  suggested_fix: string | null;
  status: AuditFindingStatus;
  dismiss_reason: string | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditFindingListResponse {
  items: AuditFindingResponse[];
  total: number;
}

export interface AuditRunDetailResponse {
  run: AuditRunResponse;
  findings: AuditFindingResponse[];
}

export interface AuditFindingRemediateResponse {
  finding_id: string;
  session_id: string;
  status: AuditFindingStatus;
}

// ---------------------------------------------------------------------------
// Paging (Sprint 33)
// ---------------------------------------------------------------------------

export type Priority = "P0" | "P1" | "P2" | "P3";
export type ResponseMode =
  | "auto_resolve"
  | "notify"
  | "page"
  | "escalate_immediate";
export type RosterPattern = "weekly" | "daily" | "custom_n_days";

export interface TeamResponse {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
}

export interface TeamListResponse {
  items: TeamResponse[];
  total: number;
}

export interface TeamCreate {
  name: string;
  slug: string;
  description?: string;
}

export interface TeamUpdate {
  name?: string;
  description?: string;
}

export interface TeamMemberResponse {
  id: string;
  team_id: string;
  user_id: string;
  role: "member" | "lead";
  added_at: string;
}

export interface TeamMemberListResponse {
  items: TeamMemberResponse[];
  total: number;
}

export interface ServiceResponse {
  id: string;
  team_id: string;
  name: string;
  slug: string;
  description: string | null;
  priority: Priority;
  preferred_mcp_server_ids: string[];
  preferred_model_config_ids: string[];
  ai_default_tier?: number | null;
  intake_url: string | null;
  external_refs: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
}

export interface ServiceListResponse {
  items: ServiceResponse[];
  total: number;
}

export interface ServiceCreate {
  team_id: string;
  name: string;
  slug: string;
  description?: string;
  priority?: Priority;
  preferred_mcp_server_ids?: string[];
  preferred_model_config_ids?: string[];
  ai_default_tier?: number | null;
  external_refs?: Record<string, unknown>;
  is_active?: boolean;
}

export interface ServiceUpdate {
  team_id?: string;
  name?: string;
  description?: string;
  priority?: Priority;
  preferred_mcp_server_ids?: string[];
  preferred_model_config_ids?: string[];
  ai_default_tier?: number | null;
  external_refs?: Record<string, unknown>;
  is_active?: boolean;
}

export interface RosterResponse {
  id: string;
  team_id: string;
  name: string;
  description: string | null;
  time_zone: string;
  pattern: RosterPattern;
  pattern_length: number;
  coverage_start_time: string;
  coverage_end_time: string;
  handoff_time: string;
  handoff_day: string | null;
  anchor_date: string;
  is_active: boolean;
  created_at: string;
}

export interface RosterListResponse {
  items: RosterResponse[];
  total: number;
}

export interface RosterCreate {
  team_id: string;
  name: string;
  description?: string;
  time_zone?: string;
  pattern?: RosterPattern;
  pattern_length?: number;
  coverage_start_time?: string;
  coverage_end_time?: string;
  handoff_time?: string;
  handoff_day?: string;
  anchor_date: string;
  is_active?: boolean;
}

export interface RosterMemberResponse {
  id: string;
  roster_id: string;
  user_id: string;
  position_index: number;
  added_at: string;
}

export interface RosterMemberListResponse {
  items: RosterMemberResponse[];
  total: number;
}

export interface RosterOverrideResponse {
  id: string;
  roster_id: string;
  covering_user_id: string;
  starts_at: string;
  ends_at: string;
  reason: string | null;
  created_by: string | null;
  created_at: string;
}

export interface PriorityRuleResponse {
  id: string;
  name: string;
  rule_index: number;
  condition: Record<string, unknown>;
  priority: Priority;
  response_mode: ResponseMode | null;
  is_active: boolean;
  created_at: string;
}

export interface PriorityRuleListResponse {
  items: PriorityRuleResponse[];
  total: number;
}

export interface PriorityRuleCreate {
  name: string;
  rule_index?: number;
  condition: Record<string, unknown>;
  priority: Priority;
  response_mode?: ResponseMode;
  is_active?: boolean;
}

export interface IncidentAssignmentResponse {
  id: string;
  incident_id: string;
  assigned_to: string;
  assigned_by: "escalation_chain" | "manual" | "self_ack" | "admin_force";
  assigned_at: string;
  released_at: string | null;
}

export interface SuppressedByMaintenanceWindow {
  id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  scope_type: MaintenanceWindowScopeType;
}

export interface IncidentPagingPanelResponse {
  incident_id: string;
  priority: Priority | null;
  response_mode: ResponseMode | null;
  service_id: string | null;
  assignment: IncidentAssignmentResponse | null;
  suppressed_by_maintenance_window: SuppressedByMaintenanceWindow | null;
}

export interface OnCallResolveResponse {
  roster_id: string;
  at: string;
  user_id: string | null;
}

export interface OnCallRangeItem {
  at: string;
  user_id: string | null;
  is_override: boolean;
  override_id: string | null;
}

export interface OnCallRangeResponse {
  roster_id: string;
  from_at: string;
  to_at: string;
  step_hours: number;
  items: OnCallRangeItem[];
}

// ---------------------------------------------------------------------------
// Escalation chains (Sprint 34)
// ---------------------------------------------------------------------------

export type EscalationTargetType = "roster" | "user" | "team";

export type ChainStatus =
  | "running"
  | "paused"
  | "acked"
  | "exhausted"
  | "resolved"
  | "cancelled";

export interface EscalationChainResponse {
  id: string;
  team_id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
}

export interface EscalationChainListResponse {
  items: EscalationChainResponse[];
  total: number;
}

export interface EscalationChainCreate {
  team_id: string;
  name: string;
  description?: string;
  is_active?: boolean;
}

export interface EscalationStepResponse {
  id: string;
  chain_id: string;
  step_index: number;
  target_type: EscalationTargetType;
  target_id: string;
  timeout_seconds: number;
  notify_channels: Record<string, unknown> | null;
}

export interface EscalationStepListResponse {
  items: EscalationStepResponse[];
  total: number;
}

export interface EscalationStepCreate {
  step_index: number;
  target_type: EscalationTargetType;
  target_id: string;
  timeout_seconds?: number;
  notify_channels?: Record<string, unknown>;
}

export interface ChainWhereUsedItem {
  service_id: string;
  service_name: string;
  team_id: string | null;
  team_name: string | null;
  applies_when: Record<string, unknown> | null;
}

export interface ChainWhereUsedResponse {
  chain_id: string;
  items: ChainWhereUsedItem[];
  total: number;
}

export type EscalationCalendarStatus =
  | "covered"
  | "unassigned"
  | "outside_coverage"
  | "disabled_roster"
  | "empty_roster"
  | "inactive_user"
  | "deleted_user"
  | "unknown";

export interface EscalationCalendarLevel {
  level: number;
  target_type: EscalationTargetType;
  target_id: string;
  target_name: string;
  resolved_user_id: string | null;
  resolved_user_name: string | null;
  resolved_user_email: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  status: EscalationCalendarStatus;
  warnings: string[];
}

export interface EscalationCalendarDay {
  date: string;
  levels: EscalationCalendarLevel[];
}

export interface EscalationCalendarResponse {
  chain_id: string;
  chain_name: string;
  team_id: string;
  team_name: string | null;
  start: string;
  end: string;
  range: "today" | "7d" | "30d" | "90d";
  days: EscalationCalendarDay[];
}

export interface IncidentPageRecord {
  id: string;
  incident_id: string;
  user_id: string;
  chain_id: string | null;
  step_index: number | null;
  channel: string;
  sent_at: string;
  ack_at: string | null;
  ack_via: string | null;
  delivery_status: string;
}

export interface IncidentChainStateRecord {
  id: string;
  incident_id: string;
  chain_id: string;
  status: ChainStatus;
  current_step_index: number;
  next_step_due_at: string | null;
  hard_deadline_at: string | null;
  pending_takeover_user_id: string | null;
  pending_takeover_expires_at: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface IncidentChainPanelResponse {
  incident_id: string;
  state: IncidentChainStateRecord | null;
  pages: IncidentPageRecord[];
  auto_start_status?: "queued" | "skipped" | "failed" | null;
  auto_start_reason?: string | null;
  resolved_tier?: number | null;
  auto_start_message?: string | null;
}

// ---------------------------------------------------------------------------
// AI incident memory (Sprint 45)
// ---------------------------------------------------------------------------

export interface IncidentMemoryResponse {
  id: string;
  org_id: string;
  service_id: string | null;
  source_incident_id: string | null;
  title: string;
  summary_md: string;
  tags: string[];
  helpful_count: number;
  unhelpful_count: number;
  is_hidden: boolean;
  review_status: "pending" | "approved" | "rejected";
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface IncidentMemoryListResponse {
  items: IncidentMemoryResponse[];
  total: number;
}

export interface IncidentMemoryCreate {
  title: string;
  summary_md: string;
  tags?: string[];
  service_id?: string | null;
}

export interface IncidentMemoryUpdate {
  title?: string;
  summary_md?: string;
  tags?: string[];
  service_id?: string | null;
  service_id_set?: boolean;
}

export interface SessionMemoriesUsedItem {
  memory: IncidentMemoryResponse;
  surfaced_at: string;
  score: number | null;
}

export interface SessionMemoriesUsedResponse {
  items: SessionMemoriesUsedItem[];
  total: number;
}

// ---------------------------------------------------------------------------
// Data retention (Sprint 53)
// ---------------------------------------------------------------------------

export type RetentionCategory =
  | "audit_entries"
  | "ingest_log"
  | "incident_memory_recall_log"
  | "bot_action_audit";

export interface RetentionCategoryConfig {
  category: string;
  ttl_days: number | null;
  last_pruned_at: string | null;
  last_pruned_count: number | null;
  is_default: boolean;
}

export interface RetentionCategoryStorage {
  category: string;
  row_count: number;
  estimated_bytes: number;
  avg_bytes_per_row: number;
  non_prunable: boolean;
}

export interface RetentionStatusResponse {
  default_ttl_days: number;
  scheduler_enabled: boolean;
  last_run_at: string | null;
  configs: RetentionCategoryConfig[];
  storage: RetentionCategoryStorage[];
}

export interface RetentionUpdateRequest {
  configs: Array<{ category: string; ttl_days: number | null }>;
}

export interface RetentionRunReportItem {
  category: string;
  ttl_days: number | null;
  cutoff: string | null;
  deleted_count: number;
  skipped_reason: string | null;
  error: string | null;
}

export interface RetentionRunReportResponse {
  started_at: string;
  finished_at: string | null;
  total_deleted: number;
  total_errors: number;
  items: RetentionRunReportItem[];
}
