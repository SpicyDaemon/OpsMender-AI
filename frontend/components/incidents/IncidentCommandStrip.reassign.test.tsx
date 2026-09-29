import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IncidentCommandStrip } from "@/components/incidents/IncidentCommandStrip";
import type {
  IncidentAssignmentResponse,
  IncidentResponderResponse,
  IncidentResponse,
  UserResponse,
} from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/context/auth", () => ({
  useAuth: () => ({
    user: { id: "user-me", username: "me", role: "operator" },
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
  getReassignOptions: vi.fn(),
  reassignIncident: vi.fn(),
  addIncidentResponders: vi.fn(),
  listTeamMembers: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

function makeIncident(status: IncidentResponse["status"] = "in_progress"): IncidentResponse {
  return {
    id: "incident-1",
    title: "Orders database is slow",
    description: "Synthetic incident for reassign tests.",
    severity: "high",
    status,
    created_at: "2026-09-28T00:00:00Z",
    updated_at: "2026-09-28T00:00:00Z",
    external_source: null,
    external_id: null,
    service_id: "service-api",
    team_id: "team-platform",
    team_name: "Platform",
    correlated_count: 0,
    flapping: false,
  };
}

function person(
  id: string,
  username: string,
  role: UserResponse["role"] = "operator",
  isActive = true,
): UserResponse {
  return {
    id,
    username,
    email: `${username}@example.test`,
    auth_source: "local",
    role,
    is_active: isActive,
    primary_org_id: "org-1",
    created_at: "2026-09-01T00:00:00Z",
  };
}

function responder(id: string, username: string): IncidentResponderResponse {
  return {
    user_id: id,
    username,
    added_by_user_id: "user-me",
    added_by_username: "me",
    added_at: "2026-09-28T00:05:00Z",
  };
}

const owner: IncidentAssignmentResponse = {
  id: "assign-1",
  incident_id: "incident-1",
  assigned_to: "u-owner",
  assigned_by: "manual",
  assigned_at: "2026-09-28T00:00:00Z",
  released_at: null,
};

function renderStrip(overrides: Partial<React.ComponentProps<typeof IncidentCommandStrip>> = {}) {
  const onChanged = vi.fn();
  render(
    <IncidentCommandStrip
      incident={makeIncident()}
      assignment={owner}
      onStartSession={vi.fn()}
      onChanged={onChanged}
      ownerLabel="owner"
      canReassign
      canManageResponders
      {...overrides}
    />,
  );
  return { onChanged };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Reassign", () => {
  it("shows Reassign only when the server allows it on an open incident", () => {
    const { unmount } = render(
      <IncidentCommandStrip
        incident={makeIncident()}
        assignment={owner}
        onStartSession={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("action-reassign")).toBeNull();
    expect(screen.queryByTestId("action-add-responders")).toBeNull();
    unmount();

    renderStrip({ incident: makeIncident("resolved") });
    expect(screen.queryByTestId("action-reassign")).toBeNull();
    expect(screen.queryByTestId("action-add-responders")).toBeNull();
  });

  it("lists the other teams with the chain that pages, and reassigns", async () => {
    apiMocks.getReassignOptions.mockResolvedValue({
      current_team_id: "team-platform",
      current_team_name: "Platform",
      pages: true,
      options: [
        {
          team_id: "team-data",
          team_name: "Data",
          chain_id: "chain-data",
          chain_name: "Data primary",
          note: null,
        },
        {
          team_id: "team-quiet",
          team_name: "Quiet",
          chain_id: null,
          chain_name: null,
          note: "This team has no active Escalation Chain, so nobody would be paged.",
        },
      ],
    });
    apiMocks.reassignIncident.mockResolvedValue({});
    const { onChanged } = renderStrip();

    fireEvent.click(screen.getByTestId("action-reassign"));
    expect(await screen.findByText("Data primary")).toBeTruthy();
    expect(
      screen.getByText("This team has no active Escalation Chain, so nobody would be paged."),
    ).toBeTruthy();
    expect(screen.getByTestId("confirm-reassign")).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByRole("radio", { name: /Data/ }));
    fireEvent.change(screen.getByLabelText(/Note for the new team/), {
      target: { value: "The orders database is theirs" },
    });
    fireEvent.click(screen.getByTestId("confirm-reassign"));

    await waitFor(() =>
      expect(apiMocks.reassignIncident).toHaveBeenCalledWith("incident-1", {
        team_id: "team-data",
        note: "The orders database is theirs",
      }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(toastSpies.success).toHaveBeenCalledWith(
      "Reassigned to Data. Paging their Escalation Chain.",
    );
  });
});

describe("Add responders", () => {
  it("is disabled once every slot is taken", () => {
    renderStrip({
      responders: [responder("r1", "ana"), responder("r2", "ben"), responder("r3", "cy")],
      responderLimit: 3,
    });
    const button = screen.getByTestId("action-add-responders");
    expect(button).toHaveProperty("disabled", true);
    expect(button.getAttribute("title")).toBe("All 3 responder slots are taken");
  });

  it("offers teammates first, never the owner, viewers, inactive people or current responders", async () => {
    apiMocks.listTeamMembers.mockResolvedValue({
      items: [
        {
          id: "m1",
          team_id: "team-platform",
          user_id: "u-teammate",
          role: "member",
          added_at: "2026-09-01T00:00:00Z",
        },
      ],
      total: 1,
    });
    apiMocks.addIncidentResponders.mockResolvedValue({ items: [], limit: 3 });
    const { onChanged } = renderStrip({
      responders: [responder("u-helping", "helping")],
      users: [
        person("u-owner", "owner"),
        person("u-admin", "ana", "admin"),
        person("u-teammate", "zed"),
        person("u-viewer", "vic", "viewer"),
        person("u-gone", "gus", "operator", false),
        person("u-helping", "helping"),
      ],
    });

    fireEvent.click(screen.getByTestId("action-add-responders"));
    expect(await screen.findByText("2 of 3 slots left.", { exact: false })).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Operator on Platform")).toBeTruthy());
    const choices = screen.getAllByRole("checkbox").map((box) => box.closest("label")?.textContent);
    expect(choices).toEqual(["zedOperator on Platform", "anaAdmin"]);

    fireEvent.click(screen.getByRole("checkbox", { name: /zed/ }));
    fireEvent.change(screen.getByLabelText(/Message/), {
      target: { value: "Can you check replica lag?" },
    });
    fireEvent.click(screen.getByTestId("confirm-add-responders"));

    await waitFor(() =>
      expect(apiMocks.addIncidentResponders).toHaveBeenCalledWith("incident-1", {
        user_ids: ["u-teammate"],
        message: "Can you check replica lag?",
      }),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(toastSpies.success).toHaveBeenCalledWith("Asked zed to help");
  });
});
