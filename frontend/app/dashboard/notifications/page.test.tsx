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
});
