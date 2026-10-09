import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const reliability = vi.hoisted(() => ({
  approveMaintenanceWindow: vi.fn(),
  createMaintenanceWindow: vi.fn(),
  deleteMaintenanceWindow: vi.fn(),
  endMaintenanceWindow: vi.fn(),
  listMaintenanceWindows: vi.fn(),
  rejectMaintenanceWindow: vi.fn(),
  updateMaintenanceWindow: vi.fn(),
}));
vi.mock("@/lib/api_reliability", () => reliability);
vi.mock("@/lib/api", () => ({}));
vi.mock("@/components/NotificationChannelsPage", () => ({
  NotificationChannelsPage: () => null,
}));
vi.mock("@/components/RosterCalendarModal", () => ({
  RosterCalendarModal: () => null,
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/context/auth", () => ({ useAuth: () => ({ user: null }) }));

import { MaintenanceWindowsPanel } from "@/components/paging/PagingShell";
import type { MaintenanceWindowResponse } from "@/lib/types";

const hour = 60 * 60 * 1000;

function makeWindow(id: string, created_by: string): MaintenanceWindowResponse {
  return {
    id,
    name: `Window ${id}`,
    reason: null,
    description: null,
    starts_at: new Date(Date.now() - hour).toISOString(),
    ends_at: new Date(Date.now() + hour).toISOString(),
    rrule: null,
    target_ids: [],
    scope_type: "global",
    scope_id: null,
    scope_ids: [],
    created_by,
    created_at: new Date().toISOString(),
    approved: true,
    approved_by: created_by,
    approved_at: new Date().toISOString(),
  } as MaintenanceWindowResponse;
}

function renderPanel(currentUserId: string, canEdit = false) {
  return render(
    <MaintenanceWindowsPanel
      windows={[makeWindow("mine", currentUserId), makeWindow("theirs", "someone-else")]}
      services={[]}
      rosters={[]}
      teams={[]}
      onChange={vi.fn()}
      canEdit={canEdit}
      currentUserId={currentUserId}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  reliability.endMaintenanceWindow.mockResolvedValue({});
});

describe("Maintenance Windows for an operator (M1-38)", () => {
  // The table renders each row's actions twice (wide and narrow layouts).
  it("offers Edit, End now and Delete on their own window only", () => {
    renderPanel("me");

    expect(screen.getAllByTitle("Edit window")).toHaveLength(2);
    expect(screen.getAllByTitle("End this window now")).toHaveLength(2);
    expect(screen.getAllByTitle("Delete")).toHaveLength(2);
  });

  it("ends their window after confirming", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel("me");

    fireEvent.click(screen.getAllByTitle("End this window now")[0]);

    await waitFor(() =>
      expect(reliability.endMaintenanceWindow).toHaveBeenCalledWith("mine"),
    );
    confirmSpy.mockRestore();
  });

  it("still offers every window to an admin", () => {
    renderPanel("admin-1", true);

    expect(screen.getAllByTitle("Edit window")).toHaveLength(4);
    expect(screen.getAllByTitle("Delete")).toHaveLength(4);
  });
});
