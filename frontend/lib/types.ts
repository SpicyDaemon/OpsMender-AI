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

export type SessionStatus = "running" | "completed" | "failed" | "paused";

export interface SessionResponse {
  id: string;
  incident_id: string | null;
  tier: number;
  model_provider: string | null;
  model_id: string | null;
  status: SessionStatus;
  summary: string | null;
  started_at: string;
  ended_at: string | null;
}

export interface SessionCreate {
  incident_id?: string;
  tier: number;
  model_provider?: string;
  model_id?: string;
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
}

export interface ConfigUpdate {
  tier?: number;
  logging_level?: string;
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
// WebSocket messages
// ---------------------------------------------------------------------------

export type WSMessageType =
  | "node_transition"
  | "tool_call"
  | "approval_requested"
  | "approval_resolved"
  | "error"
  | "session_end";

export interface WSMessage {
  type: WSMessageType;
  data: Record<string, unknown>;
}
