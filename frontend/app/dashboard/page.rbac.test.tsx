import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({ role: "viewer" }));
const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  listApprovals: vi.fn(),
  listAudit: vi.fn(),
  listIncidents: vi.fn(),
  listRosters: vi.fn(),
  listServices: vi.fn(),
  listSessions: vi.fn(),
  listTeams: vi.fn(),
  listUsers: vi.fn(),
  resolveOnCall: vi.fn(),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "user-1", username: "member", role: authState.role },
  }),
}));
vi.mock("@/context/liveEvents", () => ({ useLiveEvents: vi.fn() }));
vi.mock("@/components/ui/Toast", () => ({ useToast: () => toastSpies }));
vi.mock("@/components/SetupChecklist", () => ({
  SetupChecklist: () => <div>Setup checklist</div>,
}));
vi.mock("@/lib/api", () => apiMocks);

import DashboardIndex from "./page";

const EMPTY = { items: [], total: 0 };

beforeEach(() => {
  vi.clearAllMocks();
  authState.role = "viewer";
  apiMocks.listIncidents.mockResolvedValue(EMPTY);
  apiMocks.listApprovals.mockResolvedValue(EMPTY);
  apiMocks.listAudit.mockResolvedValue(EMPTY);
  apiMocks.listRosters.mockResolvedValue(EMPTY);
  apiMocks.listServices.mockResolvedValue(EMPTY);
  apiMocks.listSessions.mockResolvedValue(EMPTY);
  apiMocks.listTeams.mockResolvedValue(EMPTY);
  apiMocks.listUsers.mockResolvedValue(EMPTY);
  apiMocks.resolveOnCall.mockResolvedValue({ user_id: null });
});

describe("Dashboard role-aware loading", () => {
  it("does not fetch or render operator-only widgets for viewers", async () => {
    render(<DashboardIndex />);

    await waitFor(() => expect(apiMocks.listIncidents).toHaveBeenCalledOnce());
    expect(apiMocks.listSessions).not.toHaveBeenCalled();
    expect(apiMocks.listApprovals).not.toHaveBeenCalled();
    expect(apiMocks.listUsers).not.toHaveBeenCalled();
    expect(toastSpies.error).not.toHaveBeenCalled();
    expect(screen.getByText("Critical, open")).toBeTruthy();
    expect(screen.queryByText("Awaiting approval")).toBeNull();
    expect(screen.queryByText("Active AI sessions")).toBeNull();
    expect(screen.queryByText("Recent failures")).toBeNull();
    expect(screen.queryByText("Setup checklist")).toBeNull();
  });

  it("loads and renders operational widgets for operators", async () => {
    authState.role = "operator";
    render(<DashboardIndex />);

    await waitFor(() => expect(apiMocks.listSessions).toHaveBeenCalledTimes(4));
    expect(apiMocks.listApprovals).toHaveBeenCalledOnce();
    expect(apiMocks.listUsers).toHaveBeenCalledOnce();
    expect(screen.getByText("Awaiting approval")).toBeTruthy();
    expect(screen.getByText("Active AI sessions")).toBeTruthy();
    expect(screen.getByText("Recent failures")).toBeTruthy();
    expect(screen.queryByText("Setup checklist")).toBeNull();
  });
});
