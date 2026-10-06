/**
 * Someone from another team asked to join as a responder sees a banner on the
 * incident with Accept and Decline (M1-17); nobody else does.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => (k === "id" ? "inc-1" : null) }),
}));

const me = vi.hoisted(() => ({ id: "u-dee" }));
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: me.id, username: "dee", role: "operator" } }),
}));

const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
vi.mock("@/components/ui/Toast", () => ({ useToast: () => toastSpies }));

vi.mock("@/components/incidents/IncidentCommandStrip", () => ({ IncidentCommandStrip: () => null }));
vi.mock("@/components/incidents/IncidentContextRail", () => ({ IncidentContextRail: () => null }));
vi.mock("@/components/incidents/IncidentTimeline", () => ({ IncidentTimeline: () => null }));
vi.mock("@/components/sessions/IncidentSessionSidecar", () => ({ IncidentSessionSidecar: () => null }));

const request = {
  id: "req-1",
  incident_id: "inc-1",
  user_id: "u-dee",
  username: "dee",
  requested_by_user_id: "u-zed",
  requested_by_username: "zed",
  status: "pending",
  message: "Can you check the pipeline lag?",
  created_at: "2026-10-06T00:00:00Z",
  expires_at: "2026-10-06T00:30:00Z",
};

const apiMocks = vi.hoisted(() => ({
  answerResponderRequest: vi.fn(),
  createSession: vi.fn(),
  getIncident: vi.fn(),
  getIncidentPaging: vi.fn(),
  getIncidentTimeline: vi.fn(),
  listIncidentSessions: vi.fn(),
  listProviders: vi.fn(),
  listUsers: vi.fn(),
  removeIncidentResponder: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import IncidentDetailPage from "./page";

beforeEach(() => {
  vi.clearAllMocks();
  me.id = "u-dee";
  apiMocks.getIncident.mockResolvedValue({
    id: "inc-1",
    title: "Orders pipeline is slow",
    description: "lag",
    status: "in_progress",
    severity: "high",
    service_id: null,
    external_id: null,
    external_source: "manual",
    created_at: "2026-10-06T00:00:00Z",
    updated_at: "2026-10-06T00:00:00Z",
  });
  apiMocks.getIncidentPaging.mockResolvedValue({
    incident_id: "inc-1",
    priority: "P1",
    response_mode: "page",
    service_id: null,
    assignment: null,
    responders: [],
    responder_requests: [request],
    responder_limit: 3,
  });
  apiMocks.getIncidentTimeline.mockResolvedValue({ items: [] });
  apiMocks.listIncidentSessions.mockResolvedValue({ items: [] });
  apiMocks.listProviders.mockResolvedValue({ items: [] });
  apiMocks.listUsers.mockResolvedValue({ items: [], total: 0 });
  apiMocks.answerResponderRequest.mockResolvedValue({ ...request, status: "accepted" });
});

describe("Responder request banner", () => {
  it("lets the person asked accept", async () => {
    render(<IncidentDetailPage />);
    const banner = await screen.findByTestId("responder-request-banner");
    expect(banner.textContent).toContain("zed asked you to join as a responder.");
    expect(banner.textContent).toContain("Message: Can you check the pipeline lag?");

    fireEvent.click(screen.getByTestId("accept-responder-request"));
    await waitFor(() =>
      expect(apiMocks.answerResponderRequest).toHaveBeenCalledWith("inc-1", "req-1", true),
    );
    expect(toastSpies.success).toHaveBeenCalledWith("You joined the responders");
  });

  it("lets the person asked decline", async () => {
    render(<IncidentDetailPage />);
    await screen.findByTestId("responder-request-banner");
    fireEvent.click(screen.getByTestId("decline-responder-request"));
    await waitFor(() =>
      expect(apiMocks.answerResponderRequest).toHaveBeenCalledWith("inc-1", "req-1", false),
    );
  });

  it("shows nobody else a banner, only the pending request", async () => {
    me.id = "u-zed";
    render(<IncidentDetailPage />);
    expect(await screen.findByTestId("incident-responder-request")).toBeTruthy();
    expect(screen.queryByTestId("responder-request-banner")).toBeNull();
  });
});
