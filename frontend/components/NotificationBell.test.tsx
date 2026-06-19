/**
 * NotificationBell — badge rendering, dropdown contents, and "Mark all read".
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const apiMocks = vi.hoisted(() => ({
  listNotifications: vi.fn(),
  getUnreadCount: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  deleteNotification: vi.fn(),
  connectNotificationStream: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import { NotificationBell } from "@/components/NotificationBell";

const NOW = "2026-06-18T12:00:00Z";

function notif(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: "n1",
    event_type: "incident.assigned",
    category: "incident",
    title: "Incident assigned to you",
    body: "API latency spike",
    link: "/dashboard/incidents/abc",
    incident_id: "abc",
    session_id: null,
    read_at: null,
    created_at: NOW,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getUnreadCount.mockResolvedValue({ unread: 3 });
  apiMocks.listNotifications.mockResolvedValue({
    items: [notif()],
    total: 1,
    unread: 3,
  });
  apiMocks.markAllNotificationsRead.mockResolvedValue({ updated: 3 });
  apiMocks.markNotificationRead.mockResolvedValue(undefined);
  apiMocks.connectNotificationStream.mockReturnValue({ close: vi.fn() });
});

describe("NotificationBell", () => {
  it("renders the unread-count badge", async () => {
    render(<NotificationBell />);
    expect(await screen.findByText("3")).toBeTruthy();
  });

  it("caps the badge at 9+", async () => {
    apiMocks.getUnreadCount.mockResolvedValue({ unread: 42 });
    apiMocks.listNotifications.mockResolvedValue({
      items: [notif()],
      total: 42,
      unread: 42,
    });
    render(<NotificationBell />);
    expect(await screen.findByText("9+")).toBeTruthy();
  });

  it("opens the dropdown and shows items", async () => {
    render(<NotificationBell />);
    await screen.findByText("3");
    fireEvent.click(screen.getByRole("button", { name: /inbox/i }));
    expect(await screen.findByText("Incident assigned to you")).toBeTruthy();
    expect(screen.getByText("API latency spike")).toBeTruthy();
  });

  it("'Mark all read' calls the API and clears the badge", async () => {
    render(<NotificationBell />);
    await screen.findByText("3");
    fireEvent.click(screen.getByRole("button", { name: /inbox/i }));

    const markAll = await screen.findByRole("button", { name: /mark all read/i });
    fireEvent.click(markAll);

    await waitFor(() =>
      expect(apiMocks.markAllNotificationsRead).toHaveBeenCalled(),
    );
    // Badge cleared (no "3" badge text remains).
    await waitFor(() => expect(screen.queryByText("3")).toBeNull());
  });
});
