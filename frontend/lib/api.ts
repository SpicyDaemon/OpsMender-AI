/**
 * API client — thin fetch wrapper with JWT injection and 401 handling.
 * All functions throw on non-2xx responses with the API's detail message.
 */

// Defaults to empty string (same-origin) when the frontend is served by the
// backend itself. Override via NEXT_PUBLIC_API_URL at build time if the
// frontend is hosted separately.
const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

// ---------------------------------------------------------------------------
// Token storage (browser only)
// ---------------------------------------------------------------------------

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("opsmender_token");
}

export function setToken(token: string): void {
  localStorage.setItem("opsmender_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("opsmender_token");
}

export function getOrgId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("opsmender_org_id");
}

export function setOrgId(orgId: string): void {
  localStorage.setItem("opsmender_org_id", orgId);
}

export function clearOrgId(): void {
  localStorage.removeItem("opsmender_org_id");
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function request<T>(
  path: string,
  options: RequestInit = {},
  skipAuth = false,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  const token = getToken();
  if (token && !skipAuth) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const orgId = getOrgId();
  if (orgId) {
    headers["X-Org-ID"] = orgId;
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // non-JSON response
    }
    throw new Error(detail);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Convenience helpers
// ---------------------------------------------------------------------------

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown, skipAuth = false) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }, skipAuth),

  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),

  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),

  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

import type { TokenResponse, UserListResponse, UserResponse } from "./types";

export async function login(username: string, password: string): Promise<TokenResponse> {
  return api.post<TokenResponse>("/auth/login", { username, password }, true);
}

export async function register(
  username: string,
  email: string,
  password: string,
  role = "viewer",
): Promise<UserResponse> {
  return api.post<UserResponse>("/auth/register", { username, email, password, role }, true);
}

export async function getMe(): Promise<UserResponse> {
  return api.get<UserResponse>("/auth/me");
}

export async function listUsers(params?: {
  limit?: number;
  offset?: number;
}): Promise<UserListResponse> {
  const qs = new URLSearchParams();
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  const q = qs.toString();
  return api.get<UserListResponse>(`/auth/users${q ? `?${q}` : ""}`);
}

// ---------------------------------------------------------------------------
// Incidents
// ---------------------------------------------------------------------------

import type {
  IncidentCreate,
  IncidentListResponse,
  IncidentResponse,
} from "./types";

export async function listIncidents(params?: {
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<IncidentListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.q) qs.set("q", params.q);
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  const q = qs.toString();
  return api.get<IncidentListResponse>(`/incidents${q ? `?${q}` : ""}`);
}

export async function getIncident(id: string): Promise<IncidentResponse> {
  return api.get<IncidentResponse>(`/incidents/${id}`);
}

export async function createIncident(body: IncidentCreate): Promise<IncidentResponse> {
  return api.post<IncidentResponse>("/incidents", body);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

import type {
  SessionCreate,
  SessionListResponse,
  SessionMessageCreate,
  SessionMessageListResponse,
  SessionMessageResponse,
  SessionRollbackRequest,
  SessionRollbackResponse,
  SessionResponse,
} from "./types";

export async function getSession(id: string): Promise<SessionResponse> {
  return api.get<SessionResponse>(`/sessions/${id}`);
}

export async function createSession(body: SessionCreate): Promise<SessionResponse> {
  return api.post<SessionResponse>("/sessions", body);
}

export async function listIncidentSessions(id: string): Promise<SessionListResponse> {
  return api.get<SessionListResponse>(`/incidents/${id}/sessions`);
}

export async function listSessionMessages(
  id: string,
): Promise<SessionMessageListResponse> {
  return api.get<SessionMessageListResponse>(`/sessions/${id}/messages`);
}

export async function sendSessionMessage(
  id: string,
  body: SessionMessageCreate,
): Promise<SessionMessageResponse> {
  return api.post<SessionMessageResponse>(`/sessions/${id}/messages`, body);
}

export async function rollbackSession(
  id: string,
  body: SessionRollbackRequest,
): Promise<SessionRollbackResponse> {
  return api.post<SessionRollbackResponse>(`/sessions/${id}/rollback`, body);
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

import type { AuditListResponse } from "./types";

export async function listAudit(params?: {
  session_id?: string;
  tool_name?: string;
  permitted?: boolean;
  limit?: number;
  offset?: number;
}): Promise<AuditListResponse> {
  const qs = new URLSearchParams();
  if (params?.session_id) qs.set("session_id", params.session_id);
  if (params?.tool_name) qs.set("tool_name", params.tool_name);
  if (params?.permitted !== undefined) qs.set("permitted", String(params.permitted));
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  const q = qs.toString();
  return api.get<AuditListResponse>(`/audit${q ? `?${q}` : ""}`);
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

import type { ApprovalListResponse, ApprovalRequestResponse } from "./types";

export async function listApprovals(params?: {
  status?: string;
  session_id?: string;
  limit?: number;
}): Promise<ApprovalListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.session_id) qs.set("session_id", params.session_id);
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return api.get<ApprovalListResponse>(`/approvals${q ? `?${q}` : ""}`);
}

export async function approveRequest(id: string): Promise<ApprovalRequestResponse> {
  return api.post<ApprovalRequestResponse>(`/approvals/${id}/approve`);
}

export async function rejectRequest(id: string): Promise<ApprovalRequestResponse> {
  return api.post<ApprovalRequestResponse>(`/approvals/${id}/reject`);
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

import type {
  ConfigResponse,
  ConfigUpdate,
  SetupChecklistResponse,
} from "./types";

export async function getConfig(): Promise<ConfigResponse> {
  return api.get<ConfigResponse>("/config");
}

export async function updateConfig(body: ConfigUpdate): Promise<ConfigResponse> {
  return api.put<ConfigResponse>("/config", body);
}

export async function getSetupChecklist(): Promise<SetupChecklistResponse> {
  return api.get<SetupChecklistResponse>("/config/setup-checklist");
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

import type {
  ModelBootstrapStatusResponse,
  ModelConfigListResponse,
  ModelConfigResponse,
  ModelConfigSaveResponse,
  ModelConfigUpdate,
  ProviderModelsListResponse,
} from "./types";

export async function listProviders(): Promise<ProviderModelsListResponse> {
  return api.get<ProviderModelsListResponse>("/models");
}

export async function getModelBootstrapStatus(): Promise<ModelBootstrapStatusResponse> {
  return api.get<ModelBootstrapStatusResponse>("/models/bootstrap");
}

export async function setModelConfig(body: ModelConfigUpdate): Promise<ModelConfigSaveResponse> {
  return api.put<ModelConfigSaveResponse>("/config/model", body);
}

export async function listModelConfigs(): Promise<ModelConfigListResponse> {
  return api.get<ModelConfigListResponse>("/models/configs");
}

export async function createModelConfig(
  body: ModelConfigUpdate,
): Promise<ModelConfigSaveResponse> {
  return api.post<ModelConfigSaveResponse>("/models/configs", body);
}

export async function updateModelConfigById(
  id: string,
  body: ModelConfigUpdate,
): Promise<ModelConfigSaveResponse> {
  return api.put<ModelConfigSaveResponse>(`/models/configs/${id}`, body);
}

export async function deleteModelConfig(id: string): Promise<void> {
  return api.del<void>(`/models/configs/${id}`);
}

export async function setDefaultModelConfig(
  id: string,
): Promise<ModelConfigResponse> {
  return api.post<ModelConfigResponse>(`/models/configs/${id}/set-default`);
}

// ---------------------------------------------------------------------------
// MCP Servers
// ---------------------------------------------------------------------------

import type {
  MCPServerListResponse,
  MCPOAuthStartResponse,
  MCPServerResponse,
  MCPServerStatusListResponse,
  MCPServerTestResponse,
  MCPServerUpsert,
} from "./types";

export async function listMCPServers(): Promise<MCPServerListResponse> {
  return api.get<MCPServerListResponse>("/mcp-servers");
}

export async function listMCPServerStatuses(): Promise<MCPServerStatusListResponse> {
  return api.get<MCPServerStatusListResponse>("/mcp-servers/status");
}

export async function createMCPServer(
  body: MCPServerUpsert,
): Promise<MCPServerResponse> {
  return api.post<MCPServerResponse>("/mcp-servers", body);
}

export async function updateMCPServer(
  id: string,
  body: MCPServerUpsert,
): Promise<MCPServerResponse> {
  return api.put<MCPServerResponse>(`/mcp-servers/${id}`, body);
}

export async function deleteMCPServer(id: string): Promise<void> {
  return api.del<void>(`/mcp-servers/${id}`);
}

export async function testMCPServer(
  id: string,
): Promise<MCPServerTestResponse> {
  return api.post<MCPServerTestResponse>(`/mcp-servers/${id}/test`);
}

export async function startMCPOAuth(id: string): Promise<MCPOAuthStartResponse> {
  return api.get<MCPOAuthStartResponse>(
    `/mcp-servers/oauth/start?id=${encodeURIComponent(id)}`,
  );
}

// ---------------------------------------------------------------------------
// Bot Connectors
// ---------------------------------------------------------------------------

import type {
  BotConnectorListResponse,
  BotConnectorPlatformListResponse,
  BotConnectorPlatformSchema,
  BotConnectorResponse,
  BotConnectorTestResponse,
  BotConnectorUpsert,
} from "./types";

export async function listBotConnectors(params?: {
  platform?: string;
  enabled_only?: boolean;
}): Promise<BotConnectorListResponse> {
  const qs = new URLSearchParams();
  if (params?.platform) qs.set("platform", params.platform);
  if (params?.enabled_only !== undefined) {
    qs.set("enabled_only", String(params.enabled_only));
  }
  const q = qs.toString();
  return api.get<BotConnectorListResponse>(`/bot-connectors${q ? `?${q}` : ""}`);
}

export async function createBotConnector(
  body: BotConnectorUpsert,
): Promise<BotConnectorResponse> {
  return api.post<BotConnectorResponse>("/bot-connectors", body);
}

export async function updateBotConnector(
  id: string,
  body: BotConnectorUpsert,
): Promise<BotConnectorResponse> {
  return api.put<BotConnectorResponse>(`/bot-connectors/${id}`, body);
}

export async function deleteBotConnector(id: string): Promise<void> {
  return api.del<void>(`/bot-connectors/${id}`);
}

export async function testBotConnector(
  id: string,
): Promise<BotConnectorTestResponse> {
  return api.post<BotConnectorTestResponse>(`/bot-connectors/${id}/test`);
}

export async function listBotPlatformSchemas(): Promise<BotConnectorPlatformListResponse> {
  return api.get<BotConnectorPlatformListResponse>(
    "/bot-connectors/platforms",
  );
}

export async function getBotPlatformSchema(
  platform: string,
): Promise<BotConnectorPlatformSchema> {
  return api.get<BotConnectorPlatformSchema>(
    `/bot-connectors/platforms/${platform}/schema`,
  );
}

export async function startBotOAuth(
  platform: string,
  connectorId: string,
): Promise<{ authorize_url: string }> {
  return api.get<{ authorize_url: string }>(
    `/bot-connectors/oauth/${platform}/start?connector_id=${encodeURIComponent(connectorId)}`,
  );
}

// ---------------------------------------------------------------------------
// Bot User Links
// ---------------------------------------------------------------------------

import type {
  BotUserLinkCreate,
  BotUserLinkListResponse,
  BotUserLinkResponse,
} from "./types";

export async function listBotUserLinks(
  connectorId: string,
): Promise<BotUserLinkListResponse> {
  return api.get<BotUserLinkListResponse>(`/bot-connectors/${connectorId}/user-links`);
}

export async function createBotUserLink(
  connectorId: string,
  body: BotUserLinkCreate,
): Promise<BotUserLinkResponse> {
  return api.post<BotUserLinkResponse>(`/bot-connectors/${connectorId}/user-links`, body);
}

export async function deleteBotUserLink(
  connectorId: string,
  linkId: string,
): Promise<void> {
  return api.del<void>(`/bot-connectors/${connectorId}/user-links/${linkId}`);
}

// ---------------------------------------------------------------------------
// Webhook Triggers
// ---------------------------------------------------------------------------

import type {
  WebhookTriggerListResponse,
  WebhookTriggerResponse,
  WebhookTriggerTestResponse,
  WebhookTriggerUpsert,
} from "./types";

export async function listWebhookTriggers(): Promise<WebhookTriggerListResponse> {
  return api.get<WebhookTriggerListResponse>("/webhook-triggers");
}

export async function createWebhookTrigger(
  body: WebhookTriggerUpsert,
): Promise<WebhookTriggerResponse> {
  return api.post<WebhookTriggerResponse>("/webhook-triggers", body);
}

export async function updateWebhookTrigger(
  id: string,
  body: WebhookTriggerUpsert,
): Promise<WebhookTriggerResponse> {
  return api.put<WebhookTriggerResponse>(`/webhook-triggers/${id}`, body);
}

export async function deleteWebhookTrigger(id: string): Promise<void> {
  return api.del<void>(`/webhook-triggers/${id}`);
}

export async function testWebhookTrigger(
  id: string,
): Promise<WebhookTriggerTestResponse> {
  return api.post<WebhookTriggerTestResponse>(`/webhook-triggers/${id}/test`);
}

// ---------------------------------------------------------------------------
// Workflow Profiles
// ---------------------------------------------------------------------------

import type {
  AgentTeamProfileListResponse,
  AgentTeamProfileResponse,
  AgentTeamProfileUpsert,
  WorkflowProfileListResponse,
  WorkflowProfileResponse,
  WorkflowProfileUpsert,
} from "./types";

export async function listWorkflowProfiles(): Promise<WorkflowProfileListResponse> {
  return api.get<WorkflowProfileListResponse>("/workflow-profiles");
}

export async function createWorkflowProfile(
  body: WorkflowProfileUpsert,
): Promise<WorkflowProfileResponse> {
  return api.post<WorkflowProfileResponse>("/workflow-profiles", body);
}

export async function updateWorkflowProfile(
  id: string,
  body: WorkflowProfileUpsert,
): Promise<WorkflowProfileResponse> {
  return api.put<WorkflowProfileResponse>(`/workflow-profiles/${id}`, body);
}

export async function deleteWorkflowProfile(id: string): Promise<void> {
  return api.del<void>(`/workflow-profiles/${id}`);
}

// ---------------------------------------------------------------------------
// Agent Team Profiles
// ---------------------------------------------------------------------------

export async function listAgentTeamProfiles(): Promise<AgentTeamProfileListResponse> {
  return api.get<AgentTeamProfileListResponse>("/agent-team-profiles");
}

export async function createAgentTeamProfile(
  body: AgentTeamProfileUpsert,
): Promise<AgentTeamProfileResponse> {
  return api.post<AgentTeamProfileResponse>("/agent-team-profiles", body);
}

export async function updateAgentTeamProfile(
  id: string,
  body: AgentTeamProfileUpsert,
): Promise<AgentTeamProfileResponse> {
  return api.put<AgentTeamProfileResponse>(`/agent-team-profiles/${id}`, body);
}

export async function deleteAgentTeamProfile(id: string): Promise<void> {
  return api.del<void>(`/agent-team-profiles/${id}`);
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

import type {
  SkillCloneRequest,
  SkillCreate,
  SkillListResponse,
  SkillResponse,
  SkillUpdate,
} from "./types";

export async function listSkills(params?: {
  mcp_server_id?: string;
}): Promise<SkillListResponse> {
  const qs = new URLSearchParams();
  if (params?.mcp_server_id) qs.set("mcp_server_id", params.mcp_server_id);
  const q = qs.toString();
  return api.get<SkillListResponse>(`/skills${q ? `?${q}` : ""}`);
}

export async function getSkill(id: string): Promise<SkillResponse> {
  return api.get<SkillResponse>(`/skills/${id}`);
}

export async function createSkill(body: SkillCreate): Promise<SkillResponse> {
  return api.post<SkillResponse>("/skills", body);
}

export async function updateSkill(
  id: string,
  body: SkillUpdate,
): Promise<SkillResponse> {
  return api.put<SkillResponse>(`/skills/${id}`, body);
}

export async function deleteSkill(id: string): Promise<void> {
  return api.del<void>(`/skills/${id}`);
}

export async function cloneSkill(
  id: string,
  body: SkillCloneRequest,
): Promise<SkillResponse> {
  return api.post<SkillResponse>(`/skills/${id}/clone`, body);
}

export async function importSkill(params: {
  file: File;
  name?: string;
  description?: string;
  mcp_server_id?: string;
}): Promise<SkillResponse> {
  const form = new FormData();
  form.append("file", params.file);
  if (params.name) form.append("name", params.name);
  if (params.description) form.append("description", params.description);
  if (params.mcp_server_id) form.append("mcp_server_id", params.mcp_server_id);

  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}/skills/import`, {
    method: "POST",
    body: form,
    headers,
  });

  if (res.status === 401) {
    clearToken();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json() as Promise<SkillResponse>;
}

// ---------------------------------------------------------------------------
// Ingest Tokens (Sprint 14)
// ---------------------------------------------------------------------------

import type {
  IngestProviderListResponse,
  IngestTokenCreate,
  IngestTokenCreatedResponse,
  IngestTokenLearnShapeResponse,
  IngestTokenListResponse,
  IngestTokenResponse,
} from "./types";

export async function listIngestTokens(): Promise<IngestTokenListResponse> {
  return api.get<IngestTokenListResponse>("/ingest-tokens");
}

export async function createIngestToken(
  body: IngestTokenCreate,
): Promise<IngestTokenCreatedResponse> {
  return api.post<IngestTokenCreatedResponse>("/ingest-tokens", body);
}

export async function revokeIngestToken(
  id: string,
): Promise<IngestTokenResponse> {
  return api.post<IngestTokenResponse>(`/ingest-tokens/${id}/revoke`);
}

export async function deleteIngestToken(id: string): Promise<void> {
  return api.del<void>(`/ingest-tokens/${id}`);
}

export async function listIngestProviders(): Promise<IngestProviderListResponse> {
  return api.get<IngestProviderListResponse>("/ingest-providers");
}

export async function learnIngestTokenShape(
  id: string,
  payload: Record<string, unknown>,
): Promise<IngestTokenLearnShapeResponse> {
  return api.post<IngestTokenLearnShapeResponse>(
    `/ingest-tokens/${id}/learn-shape`,
    { payload },
  );
}

// ---------------------------------------------------------------------------
// WebSocket helper
// ---------------------------------------------------------------------------

export function connectSessionStream(
  sessionId: string,
  onMessage: (msg: import("./types").WSMessage) => void,
  onClose?: () => void,
): WebSocket {
  let wsBase: string;
  if (BASE_URL) {
    wsBase = BASE_URL.replace(/^http/, "ws");
  } else if (typeof window !== "undefined") {
    // Same-origin: derive ws(s):// from the current page.
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    wsBase = `${scheme}//${window.location.host}`;
  } else {
    wsBase = "";
  }
  const token = getToken();
  const url = `${wsBase}/sessions/${sessionId}/stream${token ? `?token=${token}` : ""}`;
  const ws = new WebSocket(url);

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as import("./types").WSMessage;
      onMessage(msg);
    } catch {
      // ignore malformed frames
    }
  };

  ws.onclose = () => onClose?.();

  return ws;
}

// ---------------------------------------------------------------------------
// Organizations (Phase 4)
// ---------------------------------------------------------------------------


import type {
  MyOrganizationListResponse,
  OrganizationCreate,
  OrganizationDomainCreate,
  OrganizationDomainListResponse,
  OrganizationDomainResponse,
  OrganizationListResponse,
  OrganizationResponse,
  OrganizationUpdate,
  OrganizationUserListResponse,
  OrgSAMLConfigCreate,
  OrgSAMLConfigResponse,
  OrgSSOConfigCreate,
  OrgSSOConfigResponse,
  TenantContextResponse,
  UserOrganizationLink,
} from "./types";


export async function listOrganizations(): Promise<OrganizationListResponse> {
  return api.get<OrganizationListResponse>("/organizations");
}


export async function createOrganization(
  body: OrganizationCreate,
): Promise<OrganizationResponse> {
  return api.post<OrganizationResponse>("/organizations", body);
}


export async function getOrganization(id: string): Promise<OrganizationResponse> {
  return api.get<OrganizationResponse>(`/organizations/${id}`);
}


export async function updateOrganization(
  id: string,
  body: OrganizationUpdate,
): Promise<OrganizationResponse> {
  return api.put<OrganizationResponse>(`/organizations/${id}`, body);
}


export async function deleteOrganization(id: string): Promise<void> {
  return api.del<void>(`/organizations/${id}`);
}


export async function listOrganizationUsers(
  id: string,
): Promise<OrganizationUserListResponse> {
  return api.get<OrganizationUserListResponse>(`/organizations/${id}/users`);
}


export async function addUserToOrganization(
  id: string,
  body: UserOrganizationLink,
): Promise<void> {
  return api.post<void>(`/organizations/${id}/users`, body);
}


export async function removeUserFromOrganization(
  orgId: string,
  userId: string,
): Promise<void> {
  return api.del<void>(`/organizations/${orgId}/users/${userId}`);
}


export async function listMyOrganizations(): Promise<MyOrganizationListResponse> {
  return api.get<MyOrganizationListResponse>("/auth/me/organizations");
}


export async function setMyPrimaryOrganization(
  orgId: string,
): Promise<UserResponse> {
  return api.put<UserResponse>(`/auth/me/primary-org/${orgId}`);
}


export async function listOrganizationDomains(
  orgId: string,
): Promise<OrganizationDomainListResponse> {
  return api.get<OrganizationDomainListResponse>(
    `/organizations/${orgId}/domains`,
  );
}


export async function createOrganizationDomain(
  orgId: string,
  body: OrganizationDomainCreate,
): Promise<OrganizationDomainResponse> {
  return api.post<OrganizationDomainResponse>(
    `/organizations/${orgId}/domains`,
    body,
  );
}


export async function deleteOrganizationDomain(
  orgId: string,
  domainId: string,
): Promise<void> {
  return api.del<void>(`/organizations/${orgId}/domains/${domainId}`);
}


export async function setPrimaryOrganizationDomain(
  orgId: string,
  domainId: string,
): Promise<OrganizationDomainResponse> {
  return api.post<OrganizationDomainResponse>(
    `/organizations/${orgId}/domains/${domainId}/set-primary`,
  );
}


export async function resolveTenant(): Promise<TenantContextResponse> {
  // Public — no auth required.
  return request<TenantContextResponse>("/tenant/resolve", {}, true);
}

export async function getRegistrationOpen(): Promise<{ open: boolean }> {
  // Public — no auth required. Sprint 56 Step 2: login page calls this
  // to decide whether to render the register link.
  return request<{ open: boolean }>("/auth/registration-open", {}, true);
}

// Sprint 56 — admin People-surface helpers
export async function getUser(
  id: string,
): Promise<import("./types").UserResponse> {
  return api.get<import("./types").UserResponse>(`/auth/users/${id}`);
}

export async function updateUser(
  id: string,
  body: import("./types").UserUpdateRequest,
): Promise<import("./types").UserResponse> {
  return api.patch<import("./types").UserResponse>(`/auth/users/${id}`, body);
}

export async function getUserDeletePreconditions(
  id: string,
): Promise<import("./types").SoftDeletePreconditions> {
  return api.get<import("./types").SoftDeletePreconditions>(
    `/auth/users/${id}/delete-preconditions`,
  );
}

export async function softDeleteUser(
  id: string,
): Promise<import("./types").UserResponse> {
  return api.post<import("./types").UserResponse>(
    `/auth/users/${id}/soft-delete`,
    {},
  );
}

export async function mintPasswordReset(
  id: string,
): Promise<import("./types").PasswordResetMintResponse> {
  return api.post<import("./types").PasswordResetMintResponse>(
    `/auth/users/${id}/reset-password`,
    {},
  );
}

export async function consumePasswordReset(
  token: string,
  password: string,
): Promise<void> {
  await request<void>(`/auth/password-reset/${encodeURIComponent(token)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  }, true);
}

// --- Invites (Sprint 56 Step 4) ---

export async function createInvite(
  orgId: string,
  body: import("./types").InviteCreateRequest,
): Promise<import("./types").InviteCreatedResponse> {
  return api.post<import("./types").InviteCreatedResponse>(
    `/organizations/${orgId}/invites`,
    body,
  );
}

export async function listInvites(
  orgId: string,
): Promise<import("./types").InviteListResponse> {
  return api.get<import("./types").InviteListResponse>(
    `/organizations/${orgId}/invites`,
  );
}

export async function revokeInvite(
  orgId: string,
  inviteId: string,
): Promise<import("./types").InviteResponse> {
  return api.post<import("./types").InviteResponse>(
    `/organizations/${orgId}/invites/${inviteId}/revoke`,
    {},
  );
}

export async function getInviteByToken(
  token: string,
): Promise<import("./types").InvitePublicResponse> {
  return request<import("./types").InvitePublicResponse>(
    `/invites/${encodeURIComponent(token)}`,
    {},
    true,
  );
}

export async function acceptInvite(
  token: string,
  body: import("./types").InviteAcceptRequest,
): Promise<import("./types").TokenResponse> {
  return request<import("./types").TokenResponse>(
    `/invites/${encodeURIComponent(token)}/accept`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    true,
  );
}


export async function getOrgSSOConfig(
  orgId: string,
): Promise<OrgSSOConfigResponse> {
  return api.get<OrgSSOConfigResponse>(`/organizations/${orgId}/sso`);
}


export async function upsertOrgSSOConfig(
  orgId: string,
  body: OrgSSOConfigCreate,
): Promise<OrgSSOConfigResponse> {
  return api.put<OrgSSOConfigResponse>(`/organizations/${orgId}/sso`, body);
}


export async function deleteOrgSSOConfig(orgId: string): Promise<void> {
  return api.del<void>(`/organizations/${orgId}/sso`);
}


export async function getOrgSAMLConfig(
  orgId: string,
): Promise<OrgSAMLConfigResponse> {
  return api.get<OrgSAMLConfigResponse>(`/organizations/${orgId}/saml`);
}


export async function upsertOrgSAMLConfig(
  orgId: string,
  body: OrgSAMLConfigCreate,
): Promise<OrgSAMLConfigResponse> {
  return api.put<OrgSAMLConfigResponse>(`/organizations/${orgId}/saml`, body);
}


export async function deleteOrgSAMLConfig(orgId: string): Promise<void> {
  return api.del<void>(`/organizations/${orgId}/saml`);
}

// ---------------------------------------------------------------------------
// Auditor (Sprint 32)
// ---------------------------------------------------------------------------

import type {
  AuditAnalyzerListResponse,
  AuditFindingListResponse,
  AuditFindingRemediateResponse,
  AuditFindingResponse,
  AuditRunCreate,
  AuditRunDetailResponse,
  AuditRunListResponse,
  AuditRunResponse,
} from "./types";

export async function listAuditAnalyzers(): Promise<AuditAnalyzerListResponse> {
  return api.get<AuditAnalyzerListResponse>("/audits/analyzers");
}

export async function createAuditRun(
  body: AuditRunCreate,
): Promise<AuditRunResponse> {
  return api.post<AuditRunResponse>("/audits/runs", body);
}

export async function listAuditRuns(): Promise<AuditRunListResponse> {
  return api.get<AuditRunListResponse>("/audits/runs");
}

export async function getAuditRun(
  id: string,
): Promise<AuditRunDetailResponse> {
  return api.get<AuditRunDetailResponse>(`/audits/runs/${id}`);
}

export async function listAuditFindings(params?: {
  status?: string;
  severity?: string;
  analyzer?: string;
  run_id?: string;
}): Promise<AuditFindingListResponse> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.severity) query.set("severity", params.severity);
  if (params?.analyzer) query.set("analyzer", params.analyzer);
  if (params?.run_id) query.set("run_id", params.run_id);
  const qs = query.toString();
  return api.get<AuditFindingListResponse>(
    `/audits/findings${qs ? `?${qs}` : ""}`,
  );
}

export async function remediateAuditFinding(
  id: string,
): Promise<AuditFindingRemediateResponse> {
  return api.post<AuditFindingRemediateResponse>(
    `/audits/findings/${id}/remediate`,
  );
}

export async function dismissAuditFinding(
  id: string,
  reason?: string,
): Promise<AuditFindingResponse> {
  return api.post<AuditFindingResponse>(`/audits/findings/${id}/dismiss`, {
    reason,
  });
}

// ---------------------------------------------------------------------------
// Paging (Sprint 33)
// ---------------------------------------------------------------------------

import type {
  IncidentAssignmentResponse,
  IncidentPagingPanelResponse,
  OnCallResolveResponse,
  OnCallRangeResponse,
  PriorityRuleCreate,
  PriorityRuleListResponse,
  PriorityRuleResponse,
  RosterCreate,
  RosterListResponse,
  RosterMemberListResponse,
  RosterMemberResponse,
  RosterOverrideResponse,
  RosterResponse,
  ServiceCreate,
  ServiceListResponse,
  ServiceResponse,
  ServiceUpdate,
  TeamCreate,
  TeamListResponse,
  TeamMemberListResponse,
  TeamMemberResponse,
  TeamResponse,
  TeamUpdate,
} from "./types";

export async function listTeams(): Promise<TeamListResponse> {
  return api.get<TeamListResponse>("/teams");
}
export async function createTeam(body: TeamCreate): Promise<TeamResponse> {
  return api.post<TeamResponse>("/teams", body);
}
export async function updateTeam(id: string, body: TeamUpdate): Promise<TeamResponse> {
  return api.put<TeamResponse>(`/teams/${id}`, body);
}
export async function deleteTeam(id: string): Promise<void> {
  return api.del<void>(`/teams/${id}`);
}
export async function listTeamMembers(id: string): Promise<TeamMemberListResponse> {
  return api.get<TeamMemberListResponse>(`/teams/${id}/members`);
}
export async function addTeamMember(
  id: string,
  body: { user_id: string; role?: "member" | "lead" },
): Promise<TeamMemberResponse> {
  return api.post<TeamMemberResponse>(`/teams/${id}/members`, body);
}
export async function removeTeamMember(teamId: string, userId: string): Promise<void> {
  return api.del<void>(`/teams/${teamId}/members/${userId}`);
}

export async function listServices(teamId?: string): Promise<ServiceListResponse> {
  const qs = teamId ? `?team_id=${teamId}` : "";
  return api.get<ServiceListResponse>(`/services${qs}`);
}
export async function createService(body: ServiceCreate): Promise<ServiceResponse> {
  return api.post<ServiceResponse>("/services", body);
}
export async function updateService(id: string, body: ServiceUpdate): Promise<ServiceResponse> {
  return api.put<ServiceResponse>(`/services/${id}`, body);
}
export async function deleteService(id: string): Promise<void> {
  return api.del<void>(`/services/${id}`);
}

export async function listRosters(teamId?: string): Promise<RosterListResponse> {
  const qs = teamId ? `?team_id=${teamId}` : "";
  return api.get<RosterListResponse>(`/rosters${qs}`);
}
export async function createRoster(body: RosterCreate): Promise<RosterResponse> {
  return api.post<RosterResponse>("/rosters", body);
}
export async function updateRoster(
  id: string,
  body: Partial<RosterCreate>,
): Promise<RosterResponse> {
  return api.put<RosterResponse>(`/rosters/${id}`, body);
}
export async function deleteRoster(id: string): Promise<void> {
  return api.del<void>(`/rosters/${id}`);
}
export async function listRosterMembers(id: string): Promise<RosterMemberListResponse> {
  return api.get<RosterMemberListResponse>(`/rosters/${id}/members`);
}
export async function addRosterMember(
  id: string,
  body: { user_id: string; position_index: number },
): Promise<RosterMemberResponse> {
  return api.post<RosterMemberResponse>(`/rosters/${id}/members`, body);
}
export async function removeRosterMember(
  rosterId: string,
  userId: string,
): Promise<void> {
  return api.del<void>(`/rosters/${rosterId}/members/${userId}`);
}
export async function reorderRosterMembers(
  id: string,
  orderedUserIds: string[],
): Promise<RosterMemberListResponse> {
  return api.post<RosterMemberListResponse>(`/rosters/${id}/members/reorder`, {
    ordered_user_ids: orderedUserIds,
  });
}
export async function listRosterOverrides(id: string): Promise<{
  items: RosterOverrideResponse[];
  total: number;
}> {
  return api.get(`/rosters/${id}/overrides`);
}
export async function createRosterOverride(
  id: string,
  body: {
    covering_user_id: string;
    starts_at: string;
    ends_at: string;
    reason?: string;
  },
): Promise<RosterOverrideResponse> {
  return api.post<RosterOverrideResponse>(`/rosters/${id}/overrides`, body);
}
export async function deleteRosterOverride(
  rosterId: string,
  overrideId: string,
): Promise<void> {
  return api.del<void>(`/rosters/${rosterId}/overrides/${overrideId}`);
}
export async function resolveOnCall(
  id: string,
  at?: string,
): Promise<OnCallResolveResponse> {
  const qs = at ? `?at=${encodeURIComponent(at)}` : "";
  return api.get<OnCallResolveResponse>(`/rosters/${id}/on-call${qs}`);
}

export async function resolveOnCallRange(
  id: string,
  params: { from: string; to: string; step_hours?: number },
): Promise<OnCallRangeResponse> {
  const qs = new URLSearchParams({
    from: params.from,
    to: params.to,
  });
  if (params.step_hours) qs.set("step_hours", String(params.step_hours));
  return api.get<OnCallRangeResponse>(
    `/rosters/${id}/on-call/range?${qs.toString()}`,
  );
}


export async function listPriorityRules(): Promise<PriorityRuleListResponse> {
  return api.get<PriorityRuleListResponse>("/priority-rules");
}
export async function createPriorityRule(body: PriorityRuleCreate): Promise<PriorityRuleResponse> {
  return api.post<PriorityRuleResponse>("/priority-rules", body);
}
export async function updatePriorityRule(
  id: string,
  body: Partial<PriorityRuleCreate>,
): Promise<PriorityRuleResponse> {
  return api.put<PriorityRuleResponse>(`/priority-rules/${id}`, body);
}
export async function deletePriorityRule(id: string): Promise<void> {
  return api.del<void>(`/priority-rules/${id}`);
}

export async function getIncidentPaging(
  incidentId: string,
): Promise<IncidentPagingPanelResponse> {
  return api.get<IncidentPagingPanelResponse>(`/incidents/${incidentId}/paging`);
}
export async function assignIncident(
  incidentId: string,
  userId?: string,
): Promise<IncidentAssignmentResponse> {
  return api.post<IncidentAssignmentResponse>(`/incidents/${incidentId}/assign`, {
    user_id: userId,
  });
}
export async function releaseIncident(incidentId: string): Promise<void> {
  return api.post<void>(`/incidents/${incidentId}/release`);
}

export interface IncidentBulkActionResult {
  incident_id: string;
  ok: boolean;
  error: string | null;
}

export interface IncidentBulkActionResponse {
  action: string;
  succeeded: number;
  failed: number;
  items: IncidentBulkActionResult[];
}

export async function bulkIncidentAction(
  action: "acknowledge" | "resolve" | "reassign",
  incidentIds: string[],
  userId?: string,
): Promise<IncidentBulkActionResponse> {
  return api.post<IncidentBulkActionResponse>("/incidents/bulk", {
    action,
    incident_ids: incidentIds,
    ...(userId ? { user_id: userId } : {}),
  });
}

// ---------------------------------------------------------------------------
// Escalation chains (Sprint 34)
// ---------------------------------------------------------------------------

import type {
  ChainWhereUsedResponse,
  EscalationChainCreate,
  EscalationChainListResponse,
  EscalationChainResponse,
  EscalationStepCreate,
  EscalationStepListResponse,
  EscalationStepResponse,
  IncidentChainPanelResponse,
} from "./types";

export async function listEscalationChains(
  teamId?: string,
): Promise<EscalationChainListResponse> {
  const qs = teamId ? `?team_id=${teamId}` : "";
  return api.get<EscalationChainListResponse>(`/escalation-chains${qs}`);
}

export async function createEscalationChain(
  body: EscalationChainCreate,
): Promise<EscalationChainResponse> {
  return api.post<EscalationChainResponse>("/escalation-chains", body);
}

export async function updateEscalationChain(
  id: string,
  body: Partial<EscalationChainCreate>,
): Promise<EscalationChainResponse> {
  return api.put<EscalationChainResponse>(`/escalation-chains/${id}`, body);
}

export async function deleteEscalationChain(id: string): Promise<void> {
  return api.del<void>(`/escalation-chains/${id}`);
}

export async function listEscalationSteps(
  chainId: string,
): Promise<EscalationStepListResponse> {
  return api.get<EscalationStepListResponse>(
    `/escalation-chains/${chainId}/steps`,
  );
}

export async function addEscalationStep(
  chainId: string,
  body: EscalationStepCreate,
): Promise<EscalationStepResponse> {
  return api.post<EscalationStepResponse>(
    `/escalation-chains/${chainId}/steps`,
    body,
  );
}

export async function deleteEscalationStep(
  chainId: string,
  stepId: string,
): Promise<void> {
  return api.del<void>(`/escalation-chains/${chainId}/steps/${stepId}`);
}

export async function updateEscalationStep(
  chainId: string,
  stepId: string,
  body: {
    timeout_seconds?: number;
    notify_channels?: Record<string, unknown> | null;
    notify_channels_set?: boolean;
  },
): Promise<EscalationStepResponse> {
  return api.patch<EscalationStepResponse>(
    `/escalation-chains/${chainId}/steps/${stepId}`,
    body,
  );
}

export async function reorderEscalationSteps(
  chainId: string,
  stepIds: string[],
): Promise<EscalationStepListResponse> {
  return api.post<EscalationStepListResponse>(
    `/escalation-chains/${chainId}/reorder-steps`,
    { step_ids: stepIds },
  );
}

export async function listChainServices(
  chainId: string,
): Promise<ChainWhereUsedResponse> {
  return api.get<ChainWhereUsedResponse>(
    `/escalation-chains/${chainId}/services`,
  );
}

export async function linkServiceEscalationChain(
  serviceId: string,
  chainId: string,
  appliesWhen?: Record<string, unknown>,
): Promise<void> {
  return api.post<void>(`/services/${serviceId}/escalation-chains`, {
    chain_id: chainId,
    applies_when: appliesWhen,
  });
}

export async function unlinkServiceEscalationChain(
  serviceId: string,
  chainId: string,
): Promise<void> {
  return api.del<void>(`/services/${serviceId}/escalation-chains/${chainId}`);
}

export async function getIncidentChain(
  incidentId: string,
): Promise<IncidentChainPanelResponse> {
  return api.get<IncidentChainPanelResponse>(`/incidents/${incidentId}/chain`);
}

export async function ackIncident(
  incidentId: string,
  via: "button_click" | "slash_command" | "web_ui" | "api" = "web_ui",
): Promise<IncidentChainPanelResponse> {
  return api.post<IncidentChainPanelResponse>(`/incidents/${incidentId}/ack`, {
    via,
  });
}

export async function takeIncident(
  incidentId: string,
  options: { confirm?: boolean; force?: boolean } = {},
): Promise<IncidentChainPanelResponse> {
  return api.post<IncidentChainPanelResponse>(
    `/incidents/${incidentId}/take`,
    options,
  );
}

import type {
  NotificationChannelKey,
  NotificationSettingsResponse,
  QuietHoursConfig,
  UserNotificationPrefResponse,
} from "./types";

export async function getMyNotificationPreferences(): Promise<UserNotificationPrefResponse> {
  return api.get<UserNotificationPrefResponse>(
    "/users/me/notification-preferences",
  );
}

export async function updateMyNotificationPreferences(
  body: Partial<{
    channels: Record<string, Record<string, string>>;
    routing: Record<string, NotificationChannelKey[]>;
    quiet_hours: QuietHoursConfig | null;
  }>,
): Promise<UserNotificationPrefResponse> {
  return api.put<UserNotificationPrefResponse>(
    "/users/me/notification-preferences",
    body,
  );
}

export async function getOrgNotificationSettings(
  orgId: string,
): Promise<NotificationSettingsResponse> {
  return api.get<NotificationSettingsResponse>(
    `/organizations/${orgId}/notification-settings`,
  );
}

export async function updateOrgNotificationSettings(
  orgId: string,
  notification_dedup_window_minutes: number,
): Promise<NotificationSettingsResponse> {
  return api.put<NotificationSettingsResponse>(
    `/organizations/${orgId}/notification-settings`,
    { notification_dedup_window_minutes },
  );
}

// ---------------------------------------------------------------------------
// AI incident memory (Sprint 45 Step 6)
// ---------------------------------------------------------------------------

import type {
  IncidentMemoryCreate,
  IncidentMemoryListResponse,
  IncidentMemoryResponse,
  IncidentMemoryUpdate,
  SessionMemoriesUsedResponse,
} from "./types";

export async function listMemories(params?: {
  service_id?: string;
  include_hidden?: boolean;
}): Promise<IncidentMemoryListResponse> {
  const qs = new URLSearchParams();
  if (params?.service_id) qs.set("service_id", params.service_id);
  if (params?.include_hidden) qs.set("include_hidden", "true");
  const q = qs.toString();
  return api.get<IncidentMemoryListResponse>(`/memories${q ? `?${q}` : ""}`);
}

export async function getMemory(id: string): Promise<IncidentMemoryResponse> {
  return api.get<IncidentMemoryResponse>(`/memories/${id}`);
}

export async function createMemory(
  body: IncidentMemoryCreate,
): Promise<IncidentMemoryResponse> {
  return api.post<IncidentMemoryResponse>("/memories", body);
}

export async function updateMemory(
  id: string,
  body: IncidentMemoryUpdate,
): Promise<IncidentMemoryResponse> {
  return api.put<IncidentMemoryResponse>(`/memories/${id}`, body);
}

export async function deleteMemory(id: string): Promise<void> {
  return api.del<void>(`/memories/${id}`);
}

export async function recordMemoryFeedback(
  id: string,
  helpful: boolean,
): Promise<IncidentMemoryResponse> {
  return api.post<IncidentMemoryResponse>(`/memories/${id}/feedback`, {
    helpful,
  });
}

export async function setMemoryHidden(
  id: string,
  hidden: boolean,
): Promise<IncidentMemoryResponse> {
  return api.post<IncidentMemoryResponse>(`/memories/${id}/hide`, { hidden });
}

export async function getSessionMemoriesUsed(
  sessionId: string,
): Promise<SessionMemoriesUsedResponse> {
  return api.get<SessionMemoriesUsedResponse>(
    `/sessions/${sessionId}/memories-used`,
  );
}

// ---------------------------------------------------------------------------
// Data retention (Sprint 53)
// ---------------------------------------------------------------------------

import type {
  RetentionRunReportResponse,
  RetentionStatusResponse,
  RetentionUpdateRequest,
} from "./types";

export async function getRetentionStatus(): Promise<RetentionStatusResponse> {
  return api.get<RetentionStatusResponse>("/retention");
}

export async function updateRetention(
  body: RetentionUpdateRequest,
): Promise<RetentionStatusResponse> {
  return api.put<RetentionStatusResponse>("/retention", body);
}

export async function runRetentionNow(): Promise<RetentionRunReportResponse> {
  return api.post<RetentionRunReportResponse>("/retention/run");
}
