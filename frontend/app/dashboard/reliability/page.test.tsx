import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  deleteMaintenanceWindow: vi.fn(),
  deleteSLATarget: vi.fn(),
  getSLORecommendations: vi.fn(),
  getSLASummary: vi.fn(),
  listMaintenanceWindows: vi.fn(),
  listSLATargets: vi.fn(),
}));

vi.mock("@/lib/api_reliability", () => apiMocks);

vi.mock("@/components/reliability/MaintenanceWindowModal", () => ({
  MaintenanceWindowModal: () => null,
}));

vi.mock("@/components/reliability/SLATargetModal", () => ({
  SLATargetModal: () => null,
}));

import ReliabilityPage from "@/app/dashboard/reliability/page";

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getSLASummary.mockResolvedValue({
    total_targets: 1,
    targets_up: 1,
    targets_down: 0,
    avg_uptime_30d_pct: 99.95,
    active_slo_warnings: 0,
  });
  apiMocks.getSLORecommendations.mockResolvedValue({ items: [] });
  apiMocks.listSLATargets.mockResolvedValue({
    items: [
      {
        id: "target-1",
        name: "Checkout API",
        url: "https://checkout.example.com",
        method: "GET",
        service_id: null,
        service_name: null,
        team_id: null,
        team_name: null,
        status: "up",
        uptime_30d_pct: 99.95,
        p95_ms: 120,
        slo: {
          id: "slo-1",
          target_id: "target-1",
          uptime_target_pct: 99.9,
          latency_target_ms: 500,
          window_days: 30,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
        },
        last_check_at: "2026-07-04T10:00:00Z",
        next_check_at: "2026-07-04T10:01:00Z",
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-01T00:00:00Z",
      },
    ],
    total: 1,
  });
  apiMocks.listMaintenanceWindows.mockResolvedValue({
    items: [
      {
        id: "mw-1",
        name: "Database patch",
        reason: "Quarterly patching",
        target_ids: ["*"],
        starts_at: "2026-07-04T09:00:00Z",
        ends_at: "2026-07-04T10:00:00Z",
        rrule: null,
        created_by_user_id: "u-1",
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-01T00:00:00Z",
      },
    ],
    total: 1,
  });
});

describe("Reliability page", () => {
  it("links maintenance windows to Paging and labels global targets", async () => {
    render(<ReliabilityPage />);

    await waitFor(() => expect(screen.getByText("Database patch")).toBeTruthy());

    const link = screen.getByRole("link", { name: /Paging maintenance windows/i });
    expect(link.getAttribute("href")).toBe("/dashboard/paging/maintenance-windows");
    expect(screen.getByText(/also suppresses paging/i)).toBeTruthy();
    expect(screen.getByText("All Targets")).toBeTruthy();
  });
});
