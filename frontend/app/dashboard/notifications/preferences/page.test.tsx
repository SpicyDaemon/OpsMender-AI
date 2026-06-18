import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

const apiMocks = vi.hoisted(() => ({
  getNotificationPreferences: vi.fn(),
  updateNotificationPreferences: vi.fn(),
}));
vi.mock("@/lib/api", () => apiMocks);

import NotificationPreferencesPage from "@/app/dashboard/notifications/preferences/page";

const CATEGORIES = [
  "incident",
  "approval",
  "session",
  "mention",
  "reliability",
  "account",
];

function prefs(over: Partial<Record<string, unknown>> = {}) {
  return {
    muted_categories: [],
    quiet_hours: {
      enabled: false,
      start: "22:00",
      end: "07:00",
      tz: "America/New_York",
    },
    categories: CATEGORIES,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getNotificationPreferences.mockResolvedValue(prefs());
  apiMocks.updateNotificationPreferences.mockImplementation(async (body) =>
    prefs({
      muted_categories: body.muted_categories ?? [],
      quiet_hours: body.quiet_hours ?? prefs().quiet_hours,
    }),
  );
});

describe("Notification preferences", () => {
  it("renders a toggle per fetched category", async () => {
    render(<NotificationPreferencesPage />);
    // ON (receive) by default
    const incident = await screen.findByRole("switch", { name: /Incidents/i });
    expect(incident.getAttribute("aria-checked")).toBe("true");
    // one switch per category + quiet-hours enable toggle
    const switches = screen.getAllByRole("switch");
    expect(switches.length).toBe(CATEGORIES.length + 1);
  });

  it("muting a category and saving sends it in muted_categories", async () => {
    render(<NotificationPreferencesPage />);
    const incident = await screen.findByRole("switch", { name: /Incidents/i });
    fireEvent.click(incident); // turn OFF => mute
    fireEvent.click(screen.getByRole("button", { name: /Save preferences/i }));
    await waitFor(() =>
      expect(apiMocks.updateNotificationPreferences).toHaveBeenCalled(),
    );
    const body = apiMocks.updateNotificationPreferences.mock.calls[0][0];
    expect(body.muted_categories).toContain("incident");
  });

  it("enabling quiet hours saves the window", async () => {
    render(<NotificationPreferencesPage />);
    // Wait for prefs to load, then flip the quiet-hours enable toggle (by id).
    await screen.findByRole("switch", { name: /Incidents/i });
    const quietToggle = document.getElementById("quiet-enabled")!;
    fireEvent.click(quietToggle);
    fireEvent.click(screen.getByRole("button", { name: /Save preferences/i }));
    await waitFor(() =>
      expect(apiMocks.updateNotificationPreferences).toHaveBeenCalled(),
    );
    const body = apiMocks.updateNotificationPreferences.mock.calls[0][0];
    expect(body.quiet_hours.enabled).toBe(true);
    expect(body.quiet_hours.start).toBe("22:00");
    expect(body.quiet_hours.end).toBe("07:00");
    expect(body.quiet_hours.tz).toBeTruthy();
  });
});
