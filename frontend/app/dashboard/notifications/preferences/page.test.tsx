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

// The page links admins and operators to My Routing; Viewers can't open it.
const auth = vi.hoisted(() => ({ role: "operator" }));
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { role: auth.role } }),
}));

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
  auth.role = "operator";
  apiMocks.getNotificationPreferences.mockResolvedValue(prefs());
  apiMocks.updateNotificationPreferences.mockImplementation(async (body) =>
    prefs({
      muted_categories: body.muted_categories ?? [],
      quiet_hours: body.quiet_hours ?? prefs().quiet_hours,
    }),
  );
});

describe("Inbox preferences", () => {
  it("renders a toggle per fetched category", async () => {
    render(<NotificationPreferencesPage />);
    // ON (receive) by default
    const incident = await screen.findByRole("switch", { name: /Incidents/i });
    expect(incident.getAttribute("aria-checked")).toBe("true");
    expect(incident.className).toContain("rounded-full");
    expect(incident.className).toContain("bg-status-low");
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

  it("enabling Inbox quiet hours saves the window", async () => {
    render(<NotificationPreferencesPage />);
    // Wait for prefs to load, then flip the Inbox quiet hours toggle.
    await screen.findByRole("switch", { name: /Incidents/i });
    fireEvent.click(
      screen.getByRole("switch", { name: "Enable Inbox quiet hours" }),
    );
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

  it("uses the offset-labelled timezone select for quiet hours", async () => {
    render(<NotificationPreferencesPage />);
    await screen.findByRole("switch", { name: /Incidents/i });

    expect(screen.getByRole("option", { name: /America\/New_York \([+-]\d{2}:\d{2}\)/i })).toBeTruthy();

    fireEvent.click(document.getElementById("quiet-enabled")!);
    fireEvent.change(screen.getByLabelText("Timezone"), {
      target: { value: "America/Chicago" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save preferences/i }));

    await waitFor(() =>
      expect(apiMocks.updateNotificationPreferences).toHaveBeenCalled(),
    );
    const body = apiMocks.updateNotificationPreferences.mock.calls[0][0];
    expect(body.quiet_hours.tz).toBe("America/Chicago");
  });

  it("names Inbox quiet hours and points to My Routing for pages", async () => {
    render(<NotificationPreferencesPage />);
    expect(
      await screen.findByRole("heading", { name: "Inbox quiet hours" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Inbox preferences" })).toBeTruthy();
    expect(screen.getByText(/only affect Inbox notices/).textContent).toBe(
      "Inbox quiet hours only affect Inbox notices. " +
        "Pages have their own quiet hours in My Routing.",
    );
    const link = screen.getByRole("link", { name: "My Routing" });
    expect(link.getAttribute("href")).toBe("/dashboard/paging/notifications");
  });

  it("keeps the My Routing link from Viewers, who can't open it", async () => {
    auth.role = "viewer";
    render(<NotificationPreferencesPage />);
    expect(
      await screen.findByRole("heading", { name: "Inbox quiet hours" }),
    ).toBeTruthy();
    expect(screen.getByText(/only affect Inbox notices/).textContent).toBe(
      "Inbox quiet hours only affect Inbox notices.",
    );
    expect(screen.queryByRole("link", { name: "My Routing" })).toBeNull();
  });

  it("calls them Inbox notices throughout", async () => {
    const { container } = render(<NotificationPreferencesPage />);
    await screen.findByRole("heading", { name: "Inbox quiet hours" });
    expect(
      screen.getByRole("link", { name: "Back to Inbox" }).getAttribute("href"),
    ).toBe("/dashboard/notifications");
    expect(screen.getByText(/mute its notices/).textContent).toBe(
      "Turn a category off to mute its notices. Notices in a muted " +
        "category no longer reach your Inbox or pop live.",
    );
    // One word for what lands in the Inbox, and the Inbox always named so.
    expect(container.textContent).not.toMatch(/notification/i);
    expect(container.textContent).not.toMatch(/\binbox\b/);
  });
});
