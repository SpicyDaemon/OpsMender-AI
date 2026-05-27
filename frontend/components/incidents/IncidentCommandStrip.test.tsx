import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IncidentCommandStrip } from "@/components/incidents/IncidentCommandStrip";
import type { IncidentAssignmentResponse, IncidentResponse } from "@/lib/types";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "user-me", username: "me", role: "operator" },
  }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  ackIncident: vi.fn(),
  assignIncident: vi.fn(),
  bulkIncidentAction: vi.fn(),
  releaseIncident: vi.fn(),
}));

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
    resolved_at: null,
    closed_at: null,
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
) {
  return render(
    <IncidentCommandStrip
      incident={makeIncident(status)}
      assignment={assignment}
      onStartSession={vi.fn()}
      onChanged={vi.fn()}
      ownerLabel={ownerLabel}
    />,
  );
}

describe("IncidentCommandStrip", () => {
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

  it("shows takeover copy and resolved owner label for someone else's incident", () => {
    renderStrip("in_progress", makeAssignment("user-other"), "sre-alex");

    expect(screen.queryByTestId("action-acknowledge")).toBeNull();
    expect(screen.getByTestId("action-take").textContent).toContain("Take over");
    expect(screen.getByText("Owner: sre-alex")).toBeTruthy();
    expect(screen.getByTestId("action-start-session")).toBeTruthy();
    expect(screen.getByTestId("action-resolve")).toBeTruthy();
  });

  it("shows only postmortem in resolved and closed states", () => {
    const { rerender } = render(
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

    rerender(
      <IncidentCommandStrip
        incident={makeIncident("closed")}
        assignment={null}
        onStartSession={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByTestId("action-postmortem")).toBeTruthy();
    expect(screen.queryByTestId("action-take")).toBeNull();
  });

  it("exposes polite busy-state semantics on the strip container", () => {
    renderStrip("open");

    const strip = screen.getByTestId("incident-command-strip");
    expect(strip.getAttribute("aria-live")).toBe("polite");
    expect(strip.getAttribute("aria-busy")).toBe("false");
  });
});
