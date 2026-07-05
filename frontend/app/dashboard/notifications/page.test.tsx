import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  deleteNotification: vi.fn(),
  listNotifications: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import NotificationsPage from "@/app/dashboard/notifications/page";

const BASE_NOTIFICATION = {
  event_type: "incident.updated",
  category: "incident",
  title: "Checkout API degraded",
  body: "Incident moved to in progress.",
  link: "/dashboard/incidents/detail?id=inc-1",
  incident_id: "inc-1",
  session_id: null,
  read_at: null,
  created_at: "2026-07-04T10:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.deleteNotification.mockResolvedValue(undefined);
  apiMocks.markAllNotificationsRead.mockResolvedValue({ updated: 0 });
  apiMocks.markNotificationRead.mockResolvedValue(undefined);
  apiMocks.listNotifications.mockResolvedValue({
    items: [
      { ...BASE_NOTIFICATION, id: "n-1" },
      { ...BASE_NOTIFICATION, id: "n-2", created_at: "2026-07-04T09:59:00Z" },
      {
        ...BASE_NOTIFICATION,
        id: "n-3",
        title: "Approval requested",
        body: "A Tier 1 action needs review.",
        category: "approval",
        event_type: "approval.created",
        link: "/dashboard/approvals",
        incident_id: null,
      },
    ],
    total: 3,
    unread: 3,
  });
});

describe("Notifications inbox", () => {
  it("coalesces duplicate notifications and keeps their deep link", async () => {
    render(<NotificationsPage />);

    expect(await screen.findByText("Checkout API degraded")).toBeTruthy();
    expect(screen.getAllByText("Checkout API degraded")).toHaveLength(1);
    expect(screen.getByText("×2")).toBeTruthy();

    const subjectLink = screen.getByText("Checkout API degraded").closest("a");
    expect(subjectLink?.getAttribute("href")).toBe("/dashboard/incidents/detail?id=inc-1");

    fireEvent.click(screen.getAllByRole("button", { name: /mark as read/i })[0]);
    await waitFor(() => expect(apiMocks.markNotificationRead).toHaveBeenCalledTimes(2));
    expect(apiMocks.markNotificationRead).toHaveBeenCalledWith("n-1", true);
    expect(apiMocks.markNotificationRead).toHaveBeenCalledWith("n-2", true);
  });

  it("coalesces same-content notifications from different subjects and expands to per-item links", async () => {
    // The flagship duplicate stack: identical "Queued AI session expired"
    // notifications for four *different* sessions must collapse into one row.
    apiMocks.listNotifications.mockResolvedValue({
      items: [1, 2, 3, 4].map((n) => ({
        ...BASE_NOTIFICATION,
        id: `q-${n}`,
        event_type: "session.queue_expired",
        category: "session",
        title: "Queued AI session expired",
        body: "The session was dropped after waiting too long for capacity.",
        link: `/dashboard/sessions/detail?id=sess-${n}`,
        incident_id: null,
        session_id: `sess-${n}`,
        created_at: `2026-07-04T10:0${n}:00Z`,
      })),
      total: 4,
      unread: 4,
    });
    render(<NotificationsPage />);

    expect(await screen.findByText("Queued AI session expired")).toBeTruthy();
    expect(screen.getAllByText("Queued AI session expired")).toHaveLength(1);

    // Different subjects → the row must NOT hard-link to one arbitrary session.
    expect(screen.getByText("Queued AI session expired").closest("a")).toBeNull();

    // Expanding via the ×4 chip reveals each item's own deep link.
    const toggle = screen.getByRole("button", { name: /4 duplicate notifications/i });
    fireEvent.click(toggle);
    const openLinks = screen.getAllByRole("link", { name: "Open" });
    expect(openLinks).toHaveLength(4);
    expect(openLinks.map((a) => a.getAttribute("href"))).toContain(
      "/dashboard/sessions/detail?id=sess-3",
    );

    // Following one deep link marks only that item read.
    fireEvent.click(openLinks[0]);
    await waitFor(() => expect(apiMocks.markNotificationRead).toHaveBeenCalledTimes(1));
  });
});
