import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MaintenanceWindowResponse, SLATargetResponse } from "@/lib/types";

const apiMocks = vi.hoisted(() => ({
  createMaintenanceWindow: vi.fn(),
  updateMaintenanceWindow: vi.fn(),
}));
vi.mock("@/lib/api_reliability", () => apiMocks);

import { MaintenanceWindowModal } from "./MaintenanceWindowModal";

const serviceId = "10000000-0000-0000-0000-000000000001";
function target(id: string, service: string | null): SLATargetResponse {
  return {
    id, name: service ? "Linked probe" : "Standalone probe", kind: "external",
    service_id: service, service_name: service ? "Payments" : null,
    config: null, owner_team: null, is_active: true,
    created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
    url: null, monitor_type: null, current_status: "unknown", last_check_at: null,
    uptime_30d_pct: null, active_slo_count: 0, team_id: null, team_name: null,
  };
}
const linked = target("20000000-0000-0000-0000-000000000001", serviceId);
const standalone = target("20000000-0000-0000-0000-000000000002", null);
const targets = [linked, standalone];
function window(overrides: Partial<MaintenanceWindowResponse> = {}): MaintenanceWindowResponse {
  return {
    id: "30000000-0000-0000-0000-000000000001", name: "Planned work", reason: null,
    description: null, starts_at: "2030-01-01T00:00:00Z", ends_at: "2030-01-01T01:00:00Z",
    rrule: null, target_ids: [serviceId], scope_type: "service", scope_id: serviceId,
    scope_ids: [serviceId], created_by: null, created_at: "2026-10-04T00:00:00Z",
    approved: true, approved_by: null, approved_at: null, ...overrides,
  };
}
function renderModal(initialData?: MaintenanceWindowResponse) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<MaintenanceWindowModal open onClose={onClose} onSaved={onSaved} targets={targets} initialData={initialData} />);
  return { onClose, onSaved };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.createMaintenanceWindow.mockResolvedValue({});
  apiMocks.updateMaintenanceWindow.mockResolvedValue({});
});
afterEach(cleanup);

describe("MaintenanceWindowModal scope", () => {
  it.each([
    ["*", "global", [], ["*"], /all service alerts, paging and uptime targets/i],
    [linked.id, "service", [serviceId], [linked.id], /for Payments/i],
    [standalone.id, "service", [], [standalone.id], /this uptime target only/i],
  ])("creates the explicit scope for %s", async (id, scope, scopes, ids, hint) => {
    const user = userEvent.setup();
    const callbacks = renderModal();
    await user.type(screen.getByLabelText("Name"), "Planned work");
    await user.selectOptions(screen.getByLabelText("Target"), id as string);
    expect(screen.getByText(hint as RegExp)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Schedule" }));
    expect(apiMocks.createMaintenanceWindow).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      scope_type: scope, scope_ids: scopes, target_ids: ids,
    }));
    expect(callbacks.onSaved).toHaveBeenCalledOnce();
    expect(callbacks.onClose).toHaveBeenCalledOnce();
  });

  it("maps a saved service scope back to its linked probe when editing", async () => {
    const user = userEvent.setup();
    renderModal(window());
    expect((screen.getByLabelText("Target") as HTMLSelectElement).value).toBe(linked.id);
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(apiMocks.updateMaintenanceWindow).toHaveBeenCalledExactlyOnceWith(window().id, expect.objectContaining({
      scope_type: "service", scope_ids: [serviceId], target_ids: [linked.id],
    }));
  });

  it.each([
    ["*", "global", ["*"]],
    [standalone.id, "service", [standalone.id]],
  ])("clears the old service scope when editing to %s", async (id, scope, ids) => {
    const user = userEvent.setup();
    renderModal(window());
    await user.selectOptions(screen.getByLabelText("Target"), id as string);
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(apiMocks.updateMaintenanceWindow).toHaveBeenCalledExactlyOnceWith(window().id, expect.objectContaining({
      scope_type: scope, scope_ids: [], target_ids: ids,
    }));
  });

  it("lets a person explicitly repair a legacy global window for one probe", async () => {
    const user = userEvent.setup();
    renderModal(window({ scope_type: "global", scope_id: null, scope_ids: [linked.id], target_ids: [linked.id] }));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(apiMocks.updateMaintenanceWindow).toHaveBeenCalledWith(window().id, expect.objectContaining({
      scope_type: "service", scope_ids: [serviceId], target_ids: [linked.id],
    }));
  });

  it.each([
    window({ scope_ids: [serviceId, "another-service"], target_ids: [serviceId, "another-service"] }),
    window({ scope_type: "team" }),
    window({ scope_id: null, scope_ids: ["missing-target"], target_ids: ["missing-target"] }),
  ])("requires an explicit choice for a scope the probe selector cannot represent", async (initialData) => {
    const user = userEvent.setup();
    renderModal(initialData);
    expect((screen.getByLabelText("Target") as HTMLSelectElement).value).toBe("");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(screen.getByText("Choose a target before saving this window.")).toBeTruthy();
    expect(apiMocks.updateMaintenanceWindow).not.toHaveBeenCalled();
  });
});
