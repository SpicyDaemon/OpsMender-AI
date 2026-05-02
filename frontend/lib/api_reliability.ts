import { api } from "./api";
import {
  SLATargetResponse,
  SLATargetListResponse,
  SLOListResponse,
  SLOResponse,
  MaintenanceWindowListResponse,
  MaintenanceWindowResponse,
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

export async function updateSLATarget(id: string, payload: Record<string, unknown>): Promise<SLATargetResponse> {
  return api.put(`/sla-targets/${id}`, payload);
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

export async function createSLO(payload: Record<string, unknown>): Promise<SLOResponse> {
  return api.post("/slos", payload);
}

export async function updateSLO(id: string, payload: Record<string, unknown>): Promise<SLOResponse> {
  return api.put(`/slos/${id}`, payload);
}

export async function deleteSLO(id: string): Promise<void> {
  return api.del(`/slos/${id}`);
}

export async function getSLOStatus(id: string): Promise<SLOStatusResponse> {
  return api.get(`/slos/${id}/status`);
}

export async function listMaintenanceWindows(): Promise<MaintenanceWindowListResponse> {
  return api.get("/maintenance-windows");
}

export async function createMaintenanceWindow(payload: Record<string, unknown>): Promise<MaintenanceWindowResponse> {
  return api.post("/maintenance-windows", payload);
}

export async function updateMaintenanceWindow(id: string, payload: Record<string, unknown>): Promise<MaintenanceWindowResponse> {
  return api.put(`/maintenance-windows/${id}`, payload);
}

export async function deleteMaintenanceWindow(id: string): Promise<void> {
  return api.del(`/maintenance-windows/${id}`);
}

export async function getSLATargetIncidents(id: string): Promise<IncidentResponse[]> {
  return api.get(`/sla-targets/${id}/incidents`);
}
