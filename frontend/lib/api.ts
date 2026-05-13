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
  limit?: number;
  offset?: number;
}): Promise<IncidentListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
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
  limit?: number;
}): Promise<ApprovalListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
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

import type { ConfigResponse, ConfigUpdate } from "./types";

export async function getConfig(): Promise<ConfigResponse> {
  return api.get<ConfigResponse>("/config");
}

export async function updateConfig(body: ConfigUpdate): Promise<ConfigResponse> {
  return api.put<ConfigResponse>("/config", body);
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
// Detectors
// ---------------------------------------------------------------------------

import type {
  DetectorHistoryListResponse,
  DetectorRuleCreate,
  DetectorRuleListResponse,
  DetectorRuleResponse,
  DetectorRuleUpdate,
  DetectorRunResponse,
  DetectorTemplateListResponse,
} from "./types";

export async function listDetectors(): Promise<DetectorRuleListResponse> {
  return api.get<DetectorRuleListResponse>("/detectors");
}

export async function createDetector(
  body: DetectorRuleCreate,
): Promise<DetectorRuleResponse> {
  return api.post<DetectorRuleResponse>("/detectors", body);
}

export async function updateDetector(
  id: string,
  body: DetectorRuleUpdate,
): Promise<DetectorRuleResponse> {
  return api.put<DetectorRuleResponse>(`/detectors/${id}`, body);
}

export async function deleteDetector(id: string): Promise<void> {
  return api.del<void>(`/detectors/${id}`);
}

export async function runDetector(id: string): Promise<DetectorRunResponse> {
  return api.post<DetectorRunResponse>(`/detectors/${id}/run`);
}

export async function listDetectorHistory(
  id: string,
): Promise<DetectorHistoryListResponse> {
  return api.get<DetectorHistoryListResponse>(`/detectors/${id}/history`);
}

export async function listDetectorTemplates(): Promise<DetectorTemplateListResponse> {
  return api.get<DetectorTemplateListResponse>("/detectors/templates");
}

// ---------------------------------------------------------------------------
// MCP Servers
// ---------------------------------------------------------------------------

import type {
  MCPServerListResponse,
  MCPServerResponse,
  MCPServerTestResponse,
  MCPServerUpsert,
} from "./types";

export async function listMCPServers(): Promise<MCPServerListResponse> {
  return api.get<MCPServerListResponse>("/mcp-servers");
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
