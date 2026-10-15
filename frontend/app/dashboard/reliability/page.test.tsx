import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

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

describe("Reliability page load failures", () => {
  let consoleError: MockInstance<typeof console.error>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  /** Holds the targets request open; the returned function fails it. */
  function holdTargetsRequest(): (reason: unknown) => void {
    let fail: (reason: unknown) => void = () => {};
    apiMocks.listSLATargets.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
    );
    return fail;
  }

  /** Lets a failed load settle before the console is read. */
  function settle() {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  it("logs nothing when a full page load cancels the request", async () => {
    const failRequest = holdTargetsRequest();
    render(<ReliabilityPage />);
    await waitFor(() => expect(apiMocks.listSLATargets).toHaveBeenCalledTimes(1));

    // A reload or a link out of the app never unmounts the page. The browser
    // fires pagehide, then Chromium fails the cancelled request with a plain
    // TypeError, not an AbortError.
    window.dispatchEvent(new PageTransitionEvent("pagehide"));
    failRequest(new TypeError("Failed to fetch"));
    await settle();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("logs the same TypeError while the page is still open", async () => {
    const failure = new TypeError("Failed to fetch");
    apiMocks.listSLATargets.mockRejectedValueOnce(failure);
    render(<ReliabilityPage />);
    await waitFor(() =>
      expect(consoleError).toHaveBeenCalledWith(
        "Failed to load reliability data:",
        failure,
      ),
    );
  });

  it("logs nothing for an aborted request and still finishes loading", async () => {
    apiMocks.listSLATargets.mockRejectedValueOnce(
      new DOMException("The operation was aborted.", "AbortError"),
    );
    render(<ReliabilityPage />);
    expect(await screen.findByText("No targets configured")).toBeTruthy();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("logs nothing when a load fails after moving to another page of the app", async () => {
    const failRequest = holdTargetsRequest();
    const { unmount } = render(<ReliabilityPage />);
    await waitFor(() => expect(apiMocks.listSLATargets).toHaveBeenCalledTimes(1));
    unmount();

    failRequest(new TypeError("Failed to fetch"));
    await settle();
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("refreshes again after coming back from the back/forward cache", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    apiMocks.deleteSLATarget.mockResolvedValue(undefined);
    render(<ReliabilityPage />);
    expect(await screen.findByText("Checkout API")).toBeTruthy();

    // Back from another site can show the page as it was, without mounting
    // it again.
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));

    apiMocks.listSLATargets.mockResolvedValueOnce({ items: [], total: 0 });
    fireEvent.click(screen.getByTitle("Delete target"));
    expect(await screen.findByText("No targets configured")).toBeTruthy();
    expect(apiMocks.deleteSLATarget).toHaveBeenCalledWith("target-1");
    confirmSpy.mockRestore();
  });
});
