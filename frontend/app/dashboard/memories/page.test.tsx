import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { id: "u", username: "admin", role: "admin" } }),
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

beforeEach(() => {
  vi.clearAllMocks();
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
});
