/**
 * TopBar account dropdown — structure, theme selector, and top-layer behavior.
 *
 * Regression coverage for the v1 QA bug where the account dropdown rendered
 * behind page controls (missing z-index) and the theme row clipped.
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard/incidents",
}));

const logout = vi.fn();
const MOCK_USER = {
  id: "u-1",
  username: "ada",
  email: "ada@test.com",
  role: "admin",
  auth_source: "local",
  is_active: true,
  first_name: "Ada",
  last_name: "Lovelace",
  avatar_color: null,
  primary_org_id: "org-1",
  created_at: "2026-01-01T00:00:00Z",
};
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: MOCK_USER, logout }),
}));

const setMode = vi.fn();
vi.mock("@/context/theme", () => ({
  useTheme: () => ({ mode: "system", resolvedTheme: "dark", setMode }),
}));

const apiMocks = vi.hoisted(() => ({
  getConfig: vi.fn().mockResolvedValue({ multi_org_enabled: false }),
  getOrgId: vi.fn().mockReturnValue(null),
  listApprovals: vi.fn().mockResolvedValue({ items: [] }),
  listMyOrganizations: vi.fn().mockResolvedValue({ items: [] }),
  resolveTenant: vi.fn().mockResolvedValue({ pinned: false }),
  setMyPrimaryOrganization: vi.fn().mockResolvedValue(undefined),
  setOrgId: vi.fn(),
  // NotificationBell (rendered inside TopBar) calls these on mount.
  getUnreadCount: vi.fn().mockResolvedValue({ unread: 0 }),
  listNotifications: vi.fn().mockResolvedValue({ items: [], total: 0, unread: 0 }),
  connectNotificationStream: vi.fn().mockReturnValue({ close: vi.fn() }),
}));
vi.mock("@/lib/api", () => apiMocks);

import { TopBar } from "@/components/TopBar";

function openMenu() {
  // The account button shows the display name; click it to open the menu.
  const trigger = screen.getByText("Ada Lovelace").closest("button")!;
  fireEvent.click(trigger);
}

describe("TopBar account dropdown", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.getConfig.mockResolvedValue({ multi_org_enabled: false });
    apiMocks.listApprovals.mockResolvedValue({ items: [] });
    apiMocks.listMyOrganizations.mockResolvedValue({ items: [] });
    apiMocks.resolveTenant.mockResolvedValue({ pinned: false });
  });

  it("opens on click and shows profile link, theme selector, and sign out", async () => {
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeTruthy());
    openMenu();

    expect(screen.getByText("Profile & settings")).toBeTruthy();
    expect(screen.getByText("Sign out")).toBeTruthy();
    // Theme selector with all three modes visible (no clipping of Dark).
    expect(screen.getByRole("button", { name: /system/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /light/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /dark/i })).toBeTruthy();
  });

  it("renders the dropdown as a top-layer panel (z-index + opaque surface)", async () => {
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeTruthy());
    openMenu();

    // The panel that contains the menu items must carry a high z-index and an
    // opaque background so it sits above page controls and is not click-through.
    const panel = screen.getByText("Profile & settings").closest("div.absolute");
    expect(panel).toBeTruthy();
    const cls = panel!.className;
    expect(cls).toContain("z-50");
    expect(cls).toContain("bg-bg-panel"); // opaque dark surface token
  });

  it("sign out triggers logout", async () => {
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeTruthy());
    openMenu();
    fireEvent.click(screen.getByText("Sign out"));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("clicking the theme buttons switches mode", async () => {
    render(<TopBar />);
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeTruthy());
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: /dark/i }));
    expect(setMode).toHaveBeenCalledWith("dark");
  });

  it("clicking outside closes the dropdown", async () => {
    render(
      <div>
        <TopBar />
        <button>outside</button>
      </div>,
    );
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeTruthy());
    openMenu();
    expect(screen.getByText("Profile & settings")).toBeTruthy();
    // The outside-click handler listens on mousedown.
    fireEvent.mouseDown(screen.getByText("outside"));
    await waitFor(() =>
      expect(screen.queryByText("Profile & settings")).toBeNull(),
    );
  });
});
