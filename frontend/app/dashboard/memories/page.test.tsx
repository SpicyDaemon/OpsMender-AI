import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: authState.role, role: authState.role } }),
}));

const toastSpies = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => toastSpies,
}));

const apiMocks = vi.hoisted(() => ({
  listMemories: vi.fn(),
  listServices: vi.fn(),
  listTeams: vi.fn(),
  createMemory: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  bulkDeleteMemories: vi.fn(),
  recordMemoryFeedback: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import MemoriesPage from "@/app/dashboard/memories/page";

function memory(id: string, title: string, canManage = true) {
  return {
    id,
    org_id: "o1",
    service_id: "svc1",
    source_incident_id: null,
    title,
    summary_md: "Roll the deployment.",
    tags: [],
    helpful_count: 0,
    unhelpful_count: 0,
    can_edit: canManage,
    can_delete: canManage,
    created_by_user_id: null,
    created_at: "2026-06-14T00:00:00Z",
    updated_at: "2026-06-14T00:00:00Z",
    last_used_at: null,
  };
}

function service(id: string, teamId: string, name: string) {
  return {
    id,
    team_id: teamId,
    name,
    slug: name.toLowerCase(),
    description: null,
    priority: "P2",
    mcp_server_ids: [],
    model_config_ids: [],
    ai_default_tier: null,
    intake_url: null,
    external_refs: null,
    is_active: true,
    created_at: "2026-06-14T00:00:00Z",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  authState.role = "admin";
  apiMocks.listServices.mockResolvedValue({
    items: [
      {
        id: "svc1",
        team_id: "team1",
        name: "Checkout",
        slug: "checkout",
        description: null,
        priority: "P2",
        mcp_server_ids: [],
        model_config_ids: [],
        ai_default_tier: null,
        intake_url: null,
        external_refs: null,
        is_active: true,
        created_at: "2026-06-14T00:00:00Z",
      },
    ],
    total: 1,
  });
  apiMocks.listMemories.mockResolvedValue({
    items: [memory("m1", "First lesson"), memory("m2", "Second lesson")],
    total: 2,
  });
  apiMocks.listTeams.mockResolvedValue({
    items: [{ id: "team1", name: "Payments", slug: "payments" }],
    total: 1,
  });
  apiMocks.bulkDeleteMemories.mockResolvedValue({ deleted: 2 });
  apiMocks.deleteMemory.mockResolvedValue(undefined);
});

async function renderPage() {
  render(<MemoriesPage />);
  await waitFor(() => expect(apiMocks.listMemories).toHaveBeenCalled());
}

describe("Memories selection and actions", () => {
  it("has no approval or hidden controls", async () => {
    await renderPage();
    expect(screen.queryByText(/pending review/i)).toBeNull();
    expect(screen.queryByText(/include hidden/i)).toBeNull();
    expect(screen.queryByText(/^review$/i)).toBeNull();
  });

  it("selects the current page from the header checkbox", async () => {
    await renderPage();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    expect(screen.getByText("2 selected")).toBeTruthy();
  });

  it("offers Edit and Delete for one selected memory", async () => {
    await renderPage();
    fireEvent.click(screen.getAllByRole("checkbox", { name: "Select row" })[0]);
    fireEvent.click(screen.getByTestId("memory-actions-trigger"));
    expect(
      screen.getByTestId("memory-action-edit").hasAttribute("disabled"),
    ).toBe(false);
    expect(screen.getByTestId("memory-action-delete")).toBeTruthy();
  });

  it("greys Edit and offers Delete all for multiple memories", async () => {
    await renderPage();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("memory-actions-trigger"));
    expect(
      screen.getByTestId("memory-action-edit").hasAttribute("disabled"),
    ).toBe(true);
    fireEvent.click(screen.getByTestId("memory-action-delete"));
    expect(
      screen.getByText(/Are you sure you want to delete 2 memories\?/),
    ).toBeTruthy();
    fireEvent.click(screen.getByTestId("confirm-memory-delete"));
    await waitFor(() =>
      expect(apiMocks.bulkDeleteMemories).toHaveBeenCalledWith(["m1", "m2"]),
    );
  });

  it("disables deletion for a mixed unauthorized selection", async () => {
    apiMocks.listMemories.mockResolvedValue({
      items: [
        memory("m1", "Owned lesson"),
        memory("m2", "Other team lesson", false),
      ],
      total: 2,
    });
    await renderPage();
    fireEvent.click(
      screen.getAllByRole("checkbox", {
        name: "Select all rows on this page",
      })[0],
    );
    fireEvent.click(screen.getByTestId("memory-actions-trigger"));
    expect(
      screen.getByTestId("memory-action-delete").hasAttribute("disabled"),
    ).toBe(true);
  });

  it("keeps single-row delete", async () => {
    await renderPage();
    fireEvent.click(screen.getAllByTitle("Delete")[0]);
    fireEvent.click(screen.getByTestId("confirm-memory-delete"));
    await waitFor(() => expect(apiMocks.deleteMemory).toHaveBeenCalledWith("m1"));
  });

  it("shows each memory's team, and Global for global memories", async () => {
    apiMocks.listMemories.mockResolvedValue({
      items: [memory("m1", "Team lesson"), { ...memory("m3", "Shared lesson"), service_id: null }],
      total: 2,
    });
    render(<MemoriesPage />);
    await waitFor(() => expect(screen.getAllByText("Team lesson").length).toBeGreaterThan(0));
    expect(screen.getByRole("columnheader", { name: /Team/ })).toBeTruthy();
    const teams = within(screen.getByRole("table"))
      .getAllByTestId("memory-team")
      .map((cell) => cell.textContent);
    expect(teams).toEqual(["Payments", "Global"]);
  });

  it("narrows the rows with the Team filter", async () => {
    apiMocks.listMemories.mockResolvedValue({
      items: [memory("m1", "Team lesson"), { ...memory("m3", "Shared lesson"), service_id: null }],
      total: 2,
    });
    await renderPage();
    await waitFor(() => expect(screen.getAllByText("Team lesson").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: /All Team/i }));
    expect(screen.getByLabelText("Payments")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Global"));
    await waitFor(() => expect(screen.queryAllByText("Team lesson")).toHaveLength(0));
    expect(screen.getAllByText("Shared lesson").length).toBeGreaterThan(0);
  });

  it("lets an operator edit and delete only their teams' memories", async () => {
    authState.role = "operator";
    apiMocks.listServices.mockResolvedValue({
      items: [service("svc1", "team1", "Checkout"), service("svc2", "team2", "Billing")],
      total: 2,
    });
    apiMocks.listTeams.mockResolvedValue({
      items: [
        { id: "team1", name: "Payments", slug: "payments" },
        { id: "team2", name: "Data", slug: "data" },
      ],
      total: 2,
    });
    apiMocks.listMemories.mockResolvedValue({
      items: [
        memory("m1", "Own lesson"),
        { ...memory("m2", "Other team lesson", false), service_id: "svc2" },
        { ...memory("m3", "Shared lesson", false), service_id: null },
      ],
      total: 3,
      writable_service_ids: ["svc1"],
    });
    await renderPage();
    await waitFor(() => expect(screen.getAllByText("Own lesson").length).toBeGreaterThan(0));
    const table = screen.getByRole("table");
    const rowOf = (title: string) => within(table).getByText(title).closest("tr") as HTMLElement;
    expect(within(rowOf("Own lesson")).getByTitle("Edit")).toBeTruthy();
    expect(within(rowOf("Own lesson")).getByTitle("Delete")).toBeTruthy();
    for (const title of ["Other team lesson", "Shared lesson"]) {
      expect(within(rowOf(title)).queryByTitle("Edit")).toBeNull();
      expect(within(rowOf(title)).queryByTitle("Delete")).toBeNull();
    }
    fireEvent.click(within(rowOf("Shared lesson")).getByRole("checkbox", { name: "Select row" }));
    fireEvent.click(screen.getByTestId("memory-actions-trigger"));
    expect(screen.getByTestId("memory-action-edit").hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("memory-action-delete").hasAttribute("disabled")).toBe(true);
  });

  it("offers an operator only their teams' services, never Global", async () => {
    authState.role = "operator";
    apiMocks.listServices.mockResolvedValue({
      items: [service("svc1", "team1", "Checkout"), service("svc2", "team2", "Billing")],
      total: 2,
    });
    apiMocks.listMemories.mockResolvedValue({
      items: [memory("m1", "First lesson")],
      total: 1,
      writable_service_ids: ["svc1"],
    });
    await renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: /new memory/i }))[0]);
    await waitFor(() => expect(document.getElementById("mem-service")).toBeTruthy());
    const select = document.getElementById("mem-service") as HTMLSelectElement;
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Checkout"]);
    expect(select.value).toBe("svc1");
  });

  it("keeps New memory disabled for an operator on no team", async () => {
    authState.role = "operator";
    apiMocks.listMemories.mockResolvedValue({
      items: [memory("m1", "First lesson", false)],
      total: 1,
      writable_service_ids: [],
    });
    await renderPage();
    const buttons = await screen.findAllByRole("button", { name: /new memory/i });
    expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(buttons[0].getAttribute("title")).toBe("Join a team to add memories for its services.");
  });

  it("offers an admin Global and every service", async () => {
    apiMocks.listMemories.mockResolvedValue({
      items: [memory("m1", "First lesson")],
      total: 1,
      writable_service_ids: null,
    });
    await renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: /new memory/i }))[0]);
    await waitFor(() => expect(document.getElementById("mem-service")).toBeTruthy());
    const select = document.getElementById("mem-service") as HTMLSelectElement;
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Global (applies to any service)",
      "Checkout",
    ]);
  });
});
