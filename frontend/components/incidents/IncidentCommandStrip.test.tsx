import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IncidentCommandStrip } from "@/components/incidents/IncidentCommandStrip";
import type {
  IncidentAssignmentResponse,
  IncidentResponse,
  PendingTakeover,
} from "@/lib/types";

const push = vi.fn();
const role = { current: "operator" };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "user-me", username: "me", role: role.current },
  }),
}));

const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => toastSpies,
}));

const apiMocks = vi.hoisted(() => ({
  ackIncident: vi.fn(),
  assignIncident: vi.fn(),
  bulkIncidentAction: vi.fn(),
  deleteIncident: vi.fn(),
  releaseIncident: vi.fn(),
  takeIncident: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

function makeIncident(status: IncidentResponse["status"]): IncidentResponse {
  return {
    id: "incident-1",
    title: "API latency spike",
    description: "Synthetic incident for command-strip tests.",
    severity: "high",
    status,
    created_at: "2026-05-27T00:00:00Z",
    updated_at: "2026-05-27T00:00:00Z",
    external_source: null,
    external_id: null,
    service_id: null,
    correlated_count: 0,
    flapping: false,
  };
}

function makeAssignment(
  assigned_to: string,
  released_at: string | null = null,
): IncidentAssignmentResponse {
  return {
    id: "assign-1",
    incident_id: "incident-1",
    assigned_to,
    assigned_by: "manual",
    assigned_at: "2026-05-27T00:00:00Z",
    released_at,
  };
}

function renderStrip(
  status: IncidentResponse["status"],
  assignment: IncidentAssignmentResponse | null = null,
  ownerLabel?: string | null,
  pendingTakeover?: PendingTakeover | null,
  canForceTake = false,
  canTake = true,
  canResolve = true,
) {
  return render(
    <IncidentCommandStrip
      incident={makeIncident(status)}
      assignment={assignment}
      onStartSession={vi.fn()}
      onChanged={vi.fn()}
      ownerLabel={ownerLabel}
      pendingTakeover={pendingTakeover}
      canForceTake={canForceTake}
      canTake={canTake}
      canResolve={canResolve}
    />,
  );
}

describe("IncidentCommandStrip", () => {
  beforeEach(() => {
    role.current = "operator";
    push.mockReset();
    vi.clearAllMocks();
  });

  it("hides Acknowledge, Take and Resolve from someone outside the handling team", () => {
    renderStrip("open", null, null, null, false, false, false);

    expect(screen.queryByTestId("action-acknowledge")).toBeNull();
    expect(screen.queryByTestId("action-take")).toBeNull();
    expect(screen.queryByTestId("action-resolve")).toBeNull();
    expect(screen.getByTestId("action-start-session")).toBeTruthy();
  });

  it("lets a paged responder from another team acknowledge but not resolve", () => {
    renderStrip("open", null, null, null, false, true, false);

    expect(screen.getByTestId("action-acknowledge")).toBeTruthy();
    expect(screen.getByTestId("action-take")).toBeTruthy();
    expect(screen.queryByTestId("action-resolve")).toBeNull();
  });

  it("resolves through the bulk action for the handling team", async () => {
    apiMocks.bulkIncidentAction.mockResolvedValue({});
    renderStrip("in_progress");

    fireEvent.click(screen.getByTestId("action-resolve"));
    await waitFor(() =>
      expect(apiMocks.bulkIncidentAction).toHaveBeenCalledWith("resolve", [
        "incident-1",
      ]),
    );
    expect(toastSpies.success).toHaveBeenCalledWith("Incident resolved");
  });

  it("shows the open-state action set for an unassigned incident", () => {
    renderStrip("open");

    expect(screen.getByTestId("action-acknowledge")).toBeTruthy();
    expect(screen.getByTestId("action-take")).toBeTruthy();
    expect(screen.getByTestId("action-start-session")).toBeTruthy();
    expect(screen.getByTestId("action-resolve")).toBeTruthy();
    expect(screen.queryByTestId("action-release")).toBeNull();
    expect(screen.queryByTestId("action-postmortem")).toBeNull();
  });

  it("shows release instead of take when the incident is assigned to me", () => {
    renderStrip("open", makeAssignment("user-me"));

    expect(screen.getByTestId("action-acknowledge")).toBeTruthy();
    expect(screen.getByTestId("action-release")).toBeTruthy();
    expect(screen.queryByTestId("action-take")).toBeNull();
    expect(screen.queryByTestId("action-postmortem")).toBeNull();
  });

  it("hides Acknowledge on an open incident someone else owns", () => {
    renderStrip("open", makeAssignment("user-other"), "sre-alex");

    expect(screen.queryByTestId("action-acknowledge")).toBeNull();
    expect(screen.getByTestId("action-take").textContent).toContain("Take over");
  });

  it("shows takeover copy and resolved owner label for someone else's incident", () => {
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex");

    expect(screen.queryByTestId("action-acknowledge")).toBeNull();
    expect(screen.getByTestId("action-take").textContent).toContain("Take over");
    expect(screen.getByText("Owner: sre-alex")).toBeTruthy();
    expect(screen.getByTestId("action-start-session")).toBeTruthy();
    expect(screen.getByTestId("action-resolve")).toBeTruthy();
  });

  it("requests a handover directly when someone else owns the incident", async () => {
    apiMocks.takeIncident.mockResolvedValue({});
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex");

    fireEvent.click(screen.getByTestId("action-take"));
    await waitFor(() =>
      expect(apiMocks.takeIncident).toHaveBeenCalledWith("incident-1"),
    );
    expect(apiMocks.assignIncident).not.toHaveBeenCalled();
    expect(toastSpies.success).toHaveBeenCalledWith(
      "Asked sre-alex to hand it over. The request expires in five minutes.",
    );
  });

  it("requests a handover if another owner claims an apparently unowned incident", async () => {
    apiMocks.assignIncident.mockRejectedValue(
      Object.assign(new Error("Owner holds the incident"), { status: 409 }),
    );
    apiMocks.takeIncident.mockResolvedValue({});
    renderStrip("in_progress");

    fireEvent.click(screen.getByTestId("action-take"));
    await waitFor(() =>
      expect(apiMocks.takeIncident).toHaveBeenCalledWith("incident-1"),
    );
    expect(toastSpies.success).toHaveBeenCalledWith(
      "Asked the new owner to hand it over. The request expires in five minutes.",
    );
  });

  it("shows the pending request to the owner and lets them hand over", async () => {
    apiMocks.takeIncident.mockResolvedValue({});
    renderStrip("in_progress", makeAssignment("user-me"), "me", {
      user_id: "user-other",
      username: "sre-alex",
      expires_at: "2026-09-27T20:00:00Z",
    });

    fireEvent.click(screen.getByTestId("action-hand-over"));
    await waitFor(() =>
      expect(apiMocks.takeIncident).toHaveBeenCalledWith("incident-1", {
        confirm: true,
      }),
    );
    expect(toastSpies.success).toHaveBeenCalledWith("Handed over to sre-alex");
  });

  it("shows a pending request to its requester and disables another Take", () => {
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex", {
      user_id: "user-me",
      username: "me",
      expires_at: "2026-09-27T20:00:00Z",
    });
    expect(screen.getByTestId("action-take").textContent).toContain(
      "Takeover requested",
    );
    expect((screen.getByTestId("action-take") as HTMLButtonElement).disabled).toBe(true);
  });

  it("lets an authorized teammate force take only after entering a reason", async () => {
    apiMocks.takeIncident.mockResolvedValue({});
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex", null, true);

    fireEvent.click(screen.getByTestId("action-force-take"));
    expect(screen.getByText("Force take from sre-alex")).toBeTruthy();
    expect((screen.getByTestId("confirm-force-take") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "  Emergency database errors are rising.  " },
    });
    fireEvent.click(screen.getByTestId("confirm-force-take"));
    await waitFor(() =>
      expect(apiMocks.takeIncident).toHaveBeenCalledWith("incident-1", {
        force: true,
        reason: "Emergency database errors are rising.",
      }),
    );
    expect(toastSpies.success).toHaveBeenCalledWith(
      "You now own this incident. The previous owner was notified.",
    );
  });

  it("hides force take when the server says this operator cannot use it", () => {
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex");
    expect(screen.queryByTestId("action-force-take")).toBeNull();
  });

  it("shows only postmortem in the resolved state", () => {
    render(
      <IncidentCommandStrip
        incident={makeIncident("resolved")}
        assignment={null}
        onStartSession={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByTestId("action-postmortem")).toBeTruthy();
    expect(screen.queryByTestId("action-acknowledge")).toBeNull();
    expect(screen.queryByTestId("action-start-session")).toBeNull();
    expect(screen.queryByTestId("action-resolve")).toBeNull();
    expect(screen.queryByTestId("action-take")).toBeNull();
  });

  it("exposes polite busy-state semantics on the strip container", () => {
    renderStrip("open");

    const strip = screen.getByTestId("incident-command-strip");
    expect(strip.getAttribute("aria-live")).toBe("polite");
    expect(strip.getAttribute("aria-busy")).toBe("false");
  });

  it("shows permanent delete only to admins", async () => {
    role.current = "admin";
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderStrip("open");

    fireEvent.click(screen.getByTestId("action-delete"));
    await waitFor(() =>
      expect(apiMocks.deleteIncident).toHaveBeenCalledWith("incident-1"),
    );
    expect(push).toHaveBeenCalledWith("/dashboard/incidents");
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.stringContaining("This action cannot be undone"),
    );
    confirmSpy.mockRestore();
  });

  it("hides permanent delete from operators", () => {
    renderStrip("resolved");
    expect(screen.queryByTestId("action-delete")).toBeNull();
  });

  it("shows the AI-session auto-start message returned by acknowledge", async () => {
    apiMocks.ackIncident.mockResolvedValue({
      incident_id: "incident-1",
      state: null,
      pages: [],
      auto_start_status: "queued",
      resolved_tier: 1,
      auto_start_message:
        "Incident acknowledged. AI session auto-started under Tier 1 (Approval Required).",
    });
    renderStrip("open");
    fireEvent.click(screen.getByTestId("action-acknowledge"));
    await waitFor(() =>
      expect(apiMocks.ackIncident).toHaveBeenCalledWith("incident-1", "web_ui"),
    );
    await waitFor(() =>
      expect(toastSpies.success).toHaveBeenCalledWith(
        "Incident acknowledged. AI session auto-started under Tier 1 (Approval Required).",
      ),
    );
  });

  it("warns when acknowledge reports an auto-start failure", async () => {
    apiMocks.ackIncident.mockResolvedValue({
      incident_id: "incident-1",
      state: null,
      pages: [],
      auto_start_status: "failed",
      resolved_tier: 2,
      auto_start_message:
        "Incident acknowledged. AI session auto-start failed: no enabled model configured.",
    });
    renderStrip("open");
    fireEvent.click(screen.getByTestId("action-acknowledge"));
    await waitFor(() =>
      expect(toastSpies.warning).toHaveBeenCalledWith(
        expect.stringContaining("auto-start failed: no enabled model configured"),
      ),
    );
  });
});
