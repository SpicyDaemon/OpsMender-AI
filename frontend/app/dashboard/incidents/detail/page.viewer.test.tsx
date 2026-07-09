/**
 * Viewer incident detail: shows lifecycle + responder state, but no AI session
 * internals, command strip, or action buttons (Part 1 + Part 6).
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => (k === "id" ? "inc-1" : null) }),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "v", username: "viewer", role: "viewer" } }),
}));

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

// Heavy operator/admin-only children — not rendered for viewers; stub so the
// page module loads without pulling their dependency trees.
vi.mock("@/components/incidents/IncidentCommandStrip", () => ({ IncidentCommandStrip: () => null }));
vi.mock("@/components/incidents/IncidentContextRail", () => ({ IncidentContextRail: () => null }));
vi.mock("@/components/incidents/IncidentTimeline", () => ({ IncidentTimeline: () => null }));
vi.mock("@/components/sessions/IncidentSessionSidecar", () => ({ IncidentSessionSidecar: () => null }));

const apiMocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  getIncident: vi.fn().mockResolvedValue({
    id: "inc-1",
    title: "DB outage",
    description: "down",
    status: "open",
    severity: "high",
    service_id: null,
    external_id: null,
    external_source: "manual",
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    responder_state: "assigned",
    responder_user_id: "u1",
    responder_display_name: "Alice",
  }),
  getIncidentPaging: vi.fn().mockResolvedValue(null),
  getIncidentTimeline: vi.fn().mockResolvedValue({ items: [] }),
  listIncidentSessions: vi.fn().mockResolvedValue({ items: [] }),
  listProviders: vi.fn().mockResolvedValue({ items: [] }),
  listUsers: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("@/lib/api", () => apiMocks);

import IncidentDetailPage from "./page";

describe("Viewer incident detail", () => {
  it("shows responder/lifecycle state but no actions or session internals", async () => {
    render(<IncidentDetailPage />);
    await waitFor(() => expect(screen.getByText("DB outage")).toBeTruthy());

    // Responder + read-only note are shown.
    expect(screen.getByText(/Assigned to Alice/)).toBeTruthy();
    expect(screen.getByText(/read-only access/i)).toBeTruthy();

    // No action buttons / AI session affordances.
    expect(screen.queryByRole("button", { name: /acknowledge|resolve|escalate|start session/i })).toBeNull();
    // Viewers never fetch AI session internals.
    expect(apiMocks.listIncidentSessions).not.toHaveBeenCalled();
  });
});
