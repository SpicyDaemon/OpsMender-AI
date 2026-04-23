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
  created_at: string;
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
// Detectors (Sprint 14)
// ---------------------------------------------------------------------------

export interface DetectorRuleResponse {
  id: string;
  name: string;
  mcp_server_id: string;
  prompt_template: string;
  model_config_id: string | null;
  interval_seconds: number;
  severity_default: Severity;
  is_active: boolean;
  last_ran_at: string | null;
  last_fingerprint: string | null;
  created_at: string;
  updated_at: string;
}

export interface DetectorRuleListResponse {
  items: DetectorRuleResponse[];
  total: number;
}

export interface DetectorRuleCreate {
  name: string;
  mcp_server_id: string;
  prompt_template: string;
  model_config_id?: string | null;
  interval_seconds?: number;
  severity_default?: Severity;
  is_active?: boolean;
}

export interface DetectorRuleUpdate {
  name?: string;
  mcp_server_id?: string;
  prompt_template?: string;
  model_config_id?: string | null;
  interval_seconds?: number;
  severity_default?: Severity;
  is_active?: boolean;
}

export interface DetectorHistoryResponse {
  id: string;
  rule_id: string;
  ran_at: string;
  duration_ms: number | null;
  issue_detected: boolean;
  incident_id: string | null;
  raw_verdict: Record<string, unknown> | null;
  error: string | null;
}

export interface DetectorHistoryListResponse {
  items: DetectorHistoryResponse[];
  total: number;
}

export interface DetectorRunResponse {
  success: boolean;
  issue_detected: boolean;
  incident_id: string | null;
  error: string | null;
}

export interface DetectorTemplateResponse {
  key: string;
  label: string;
  description: string;
  prompt_template: string;
  severity_default: Severity;
  interval_seconds: number;
}

export interface DetectorTemplateListResponse {
  items: DetectorTemplateResponse[];
  total: number;
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
