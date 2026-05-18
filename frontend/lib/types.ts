// TypeScript types matching backend Pydantic schemas exactly.

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  username: string;
  email: string;
  role: "admin" | "operator" | "viewer";
  is_active: boolean;
  primary_org_id: string | null;
  created_at: string;
}

export interface UserListResponse {
  items: UserResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Incidents
// ---------------------------------------------------------------------------

export type Severity = "critical" | "high" | "medium" | "low";
export type IncidentStatus = "open" | "in_progress" | "resolved" | "closed";

export interface IncidentResponse {
  id: string;
  title: string;
  description: string;
  status: IncidentStatus;
  severity: Severity | null;
  external_id: string | null;
  external_source: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentListResponse {
  items: IncidentResponse[];
  total: number;
}

export interface IncidentCreate {
  title: string;
  description: string;
  severity?: Severity;
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export type SessionStatus =
  | "active"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "timed_out";

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
  tier: number;
  model_provider?: string;
  model_id?: string;
  initial_briefing?: string;
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

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

export interface ApprovalRequestResponse {
  id: string;
  session_id: string;
  action: Record<string, unknown>;
  justification: string | null;
  status: ApprovalStatus;
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

export interface ConfigResponse {
  tier: number;
  mcp_servers: Record<string, unknown>[];
  audit_output: string;
  logging_level: string;
  ingest_auto_start_enabled: boolean;
  ingest_auto_start_min_severity: Severity;
  ingest_auto_start_source: string | null;
}

export interface ConfigUpdate {
  tier?: number;
  logging_level?: string;
  ingest_auto_start_enabled?: boolean;
  ingest_auto_start_min_severity?: Severity;
  ingest_auto_start_source?: string;
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
  max_tokens: number;
  temperature: number;
  is_default: boolean;
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
}

export interface MCPServerListResponse {
  items: MCPServerResponse[];
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

// ---------------------------------------------------------------------------
// Bot Connectors
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
  status?: BotConnectorStatus;
  is_enabled?: boolean;
}

export interface BotConnectorTestResponse {
  success: boolean;
  detail: string;
  status: BotConnectorStatus;
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
}

export interface BotConnectorPlatformListResponse {
  items: BotConnectorPlatformSchema[];
  total: number;
}

// ---------------------------------------------------------------------------
// Webhook Triggers
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

export interface SkillResponse {
  id: string;
  name: string;
  description: string | null;
  mcp_server_id: string | null;
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
}

export interface SkillUpdate {
  name: string;
  content_md: string;
  description?: string | null;
  mcp_server_id?: string | null;
}

export interface SkillCloneRequest {
  name: string;
  mcp_server_id?: string | null;
  description?: string | null;
}

// ---------------------------------------------------------------------------
// Ingest Tokens (Sprint 14)
// ---------------------------------------------------------------------------

export type IngestProvider =
  | "auto"
  | "cloudwatch"
  | "azure_monitor"
  | "legacy_alert_vendor"
  | "legacy_alert_relay"
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
  | "session_end";

export interface WSMessage {
  type: WSMessageType;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Reliability (Sprint 25)
// ---------------------------------------------------------------------------

export type SLATargetKind = "http" | "tcp" | "external";

export interface SLATargetResponse {
  id: string;
  name: string;
  kind: SLATargetKind;
  config: Record<string, unknown> | null;
  owner_team: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
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

export interface QuietHoursWindow {
  start: string;
  end: string;
}

export interface QuietHoursConfig {
  weekday?: QuietHoursWindow | null;
  weekend?: QuietHoursWindow | null;
  min_priority_to_break?: Priority | null;
  time_zone?: string | null;
}

export interface UserNotificationPrefResponse {
  user_id: string;
  org_id: string;
  channels: Record<string, Record<string, string>>;
  routing: Record<string, NotificationChannelKey[]>;
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
  created_by: string | null;
  created_at: string;
}

export interface MaintenanceWindowListResponse {
  items: MaintenanceWindowResponse[];
  total: number;
}

export interface UptimeSeriesPoint {
  ts: string;
  up_pct: number;
}

export interface SLATargetUptimeResponse {
  uptime_pct: number;
  total_samples: number;
  up_samples: number;
  downtime_seconds: number;
  suppressed_seconds: number;
  series: UptimeSeriesPoint[];
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
  opsmender_user_id: string;
  opsmender_username: string;
  opsmender_role: string;
  created_at: string;
}

export interface BotUserLinkListResponse {
  items: BotUserLinkResponse[];
  total: number;
}

export interface BotUserLinkCreate {
  platform_user_id: string;
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
  external_refs?: Record<string, unknown>;
  is_active?: boolean;
}

export interface ServiceUpdate {
  team_id?: string;
  name?: string;
  description?: string;
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
}
