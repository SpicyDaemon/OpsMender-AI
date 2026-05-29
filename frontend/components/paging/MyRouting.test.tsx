import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Mock the API surface used by the My Routing panel.
const getMyNotificationPreferences = vi.fn();
const listBotConnectors = vi.fn();
const updateMyNotificationPreferences = vi.fn();
const testMyNotificationPreferences = vi.fn();

vi.mock("@/lib/api", () => ({
  getMyNotificationPreferences: () => getMyNotificationPreferences(),
  listBotConnectors: () => listBotConnectors(),
  updateMyNotificationPreferences: (body: unknown) =>
    updateMyNotificationPreferences(body),
  testMyNotificationPreferences: () => testMyNotificationPreferences(),
}));

// Stub the embedded pages so we don't pull in their heavy dependency graph.
vi.mock("@/components/NotificationChannelsPage", () => ({
  NotificationChannelsPage: () => null,
}));
vi.mock("@/components/OutboundHooksPage", () => ({
  OutboundHooksPage: () => null,
}));
vi.mock("@/components/RosterCalendarModal", () => ({
  RosterCalendarModal: () => null,
}));
vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import {
  ChannelMultiSelect,
  NotificationPreferencesPanel,
} from "@/components/paging/PagingShell";

const basePref = {
  user_id: "u1",
  org_id: "o1",
  channels: {},
  routing: { P0: ["slack_dm", "email"], P1: ["email"] },
  quiet_hours: null as unknown,
  updated_at: new Date().toISOString(),
};

beforeEach(() => {
  vi.clearAllMocks();
  getMyNotificationPreferences.mockResolvedValue(basePref);
  listBotConnectors.mockResolvedValue({
    items: [{ id: "c1", platform: "slack" }],
    total: 1,
  });
  updateMyNotificationPreferences.mockResolvedValue(basePref);
  testMyNotificationPreferences.mockResolvedValue({ results: [], tested: 0 });
});

describe("ChannelMultiSelect", () => {
  it("uses checkboxes and toggles a channel without Ctrl/Cmd", () => {
    const onToggle = vi.fn();
    render(
      <ChannelMultiSelect
        options={[
          { key: "slack_dm", label: "Slack DM" },
          { key: "email", label: "Email" },
        ]}
        selected={[]}
        onToggle={onToggle}
      />,
    );
    // Opens the popover.
    fireEvent.click(screen.getByRole("button", { name: /do not notify/i }));
    fireEvent.click(screen.getByLabelText("Email"));
    expect(onToggle).toHaveBeenCalledWith("email", true);
  });
});

describe("NotificationPreferencesPanel (My Routing)", () => {
  it("renders the four priority rows", async () => {
    render(<NotificationPreferencesPanel />);
    expect(await screen.findByText("Critical")).toBeTruthy();
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("Medium")).toBeTruthy();
    expect(screen.getByText("Low")).toBeTruthy();
    expect(screen.getByRole("button", { name: /test notification/i })).toBeTruthy();
  });

  it("shows the empty channel state + CTA when no channels are configured", async () => {
    listBotConnectors.mockResolvedValue({ items: [], total: 0 });
    const onGoToChannels = vi.fn();
    render(<NotificationPreferencesPanel onGoToChannels={onGoToChannels} />);
    expect(
      await screen.findByText(/No notification channels are configured yet/i),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: /Go to Notification Channels/i }),
    );
    expect(onGoToChannels).toHaveBeenCalled();
  });

  it("renders quiet hours with the P0 bypass note and saves min_priority P0", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      quiet_hours: {
        weekday_start: "22:00",
        weekday_end: "07:00",
        days: [0, 1, 2, 3, 4],
        time_zone: "UTC",
      },
    });
    render(<NotificationPreferencesPanel />);
    expect(await screen.findByText("Quiet Hours")).toBeTruthy();
    expect(
      screen.getByText(/P0 \(Critical\) always/i),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() => expect(updateMyNotificationPreferences).toHaveBeenCalled());
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    expect(body.quiet_hours.min_priority_to_break).toBe("P0");
    expect(body.quiet_hours.weekday_start).toBe("22:00");
  });
});
