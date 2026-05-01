import { api } from "./api";
import {
  SLATargetResponse,
  SLATargetListResponse,
  SLOListResponse,
  MaintenanceWindowListResponse,
  SLATargetUptimeResponse,
  SLOStatusResponse,
  IncidentResponse,
} from "./types";

export async function listSLATargets(): Promise<SLATargetListResponse> {
  return api.get("/sla-targets");
}

export async function getSLATarget(id: string): Promise<SLATargetResponse> {
  return api.get(`/sla-targets/${id}`);
}

export async function createSLATarget(payload: Record<string, unknown>): Promise<SLATargetResponse> {
  return api.post("/sla-targets", payload);
}

export async function deleteSLATarget(id: string): Promise<void> {
  return api.del(`/sla-targets/${id}`);
}

export async function getSLATargetUptime(id: string, window: string = "30d"): Promise<SLATargetUptimeResponse> {
  return api.get(`/sla-targets/${id}/uptime?window=${window}`);
}

export async function listSLOs(): Promise<SLOListResponse> {
  return api.get("/slos");
}

export async function getSLOStatus(id: string): Promise<SLOStatusResponse> {
  return api.get(`/slos/${id}/status`);
}

export async function listMaintenanceWindows(): Promise<MaintenanceWindowListResponse> {
  return api.get("/maintenance-windows");
}

export async function getSLATargetIncidents(id: string): Promise<IncidentResponse[]> {
  return api.get(`/sla-targets/${id}/incidents`);
}
