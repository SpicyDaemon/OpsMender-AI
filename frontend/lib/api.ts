/**
 * API client — thin fetch wrapper with JWT injection and 401 handling.
 * All functions throw on non-2xx responses with the API's detail message.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Token storage (browser only)
// ---------------------------------------------------------------------------

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("aim_token");
}

export function setToken(token: string): void {
  localStorage.setItem("aim_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("aim_token");
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

import type { TokenResponse, UserResponse } from "./types";

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

import type { SessionCreate, SessionResponse } from "./types";

export async function getSession(id: string): Promise<SessionResponse> {
  return api.get<SessionResponse>(`/sessions/${id}`);
}

export async function createSession(body: SessionCreate): Promise<SessionResponse> {
  return api.post<SessionResponse>("/sessions", body);
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
  ModelConfigListResponse,
  ModelConfigResponse,
  ModelConfigUpdate,
  ProviderModelsListResponse,
} from "./types";

export async function listProviders(): Promise<ProviderModelsListResponse> {
  return api.get<ProviderModelsListResponse>("/models");
}

export async function setModelConfig(body: ModelConfigUpdate): Promise<unknown> {
  return api.put("/config/model", body);
}

export async function listModelConfigs(): Promise<ModelConfigListResponse> {
  return api.get<ModelConfigListResponse>("/models/configs");
}

export async function createModelConfig(
  body: ModelConfigUpdate,
): Promise<ModelConfigResponse> {
  return api.post<ModelConfigResponse>("/models/configs", body);
}

export async function updateModelConfigById(
  id: string,
  body: ModelConfigUpdate,
): Promise<ModelConfigResponse> {
  return api.put<ModelConfigResponse>(`/models/configs/${id}`, body);
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
// WebSocket helper
// ---------------------------------------------------------------------------

export function connectSessionStream(
  sessionId: string,
  onMessage: (msg: import("./types").WSMessage) => void,
  onClose?: () => void,
): WebSocket {
  const wsBase = BASE_URL.replace(/^http/, "ws");
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
