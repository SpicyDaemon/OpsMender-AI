import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
  // Legacy shape — should be read as Stage 1/2 (backward compatibility).
  routing: { P0: ["slack_dm", "email"], P1: ["email"] },
  quiet_hours: null as unknown,
  updated_at: new Date().toISOString(),
};

const CONNECTORS = [
  { id: "c-slack", name: "Slack NOC", platform: "slack", is_enabled: true },
  { id: "c-tg", name: "Telegram Ops", platform: "telegram", is_enabled: true },
  { id: "c-off", name: "Disabled Discord", platform: "discord", is_enabled: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  getMyNotificationPreferences.mockResolvedValue(basePref);
  listBotConnectors.mockResolvedValue({ items: CONNECTORS, total: CONNECTORS.length });
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
    fireEvent.click(screen.getByRole("button", { name: /do not notify/i }));
    fireEvent.click(screen.getByLabelText("Email"));
    expect(onToggle).toHaveBeenCalledWith("email", true);
  });
});

describe("NotificationPreferencesPanel (My Routing — staged)", () => {
  it("renders the four priority rows", async () => {
    render(<NotificationPreferencesPanel />);
    expect(await screen.findByText("Critical")).toBeTruthy();
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("Medium")).toBeTruthy();
    expect(screen.getByText("Low")).toBeTruthy();
    expect(screen.getByRole("button", { name: /test notification/i })).toBeTruthy();
  });

  it("normalizes legacy routing into ordered stages", async () => {
    render(<NotificationPreferencesPanel />);
    // P0 legacy ["slack_dm","email"] → Stage 1 + Stage 2; P1 ["email"] → Stage 1.
    expect((await screen.findAllByText("Stage 1")).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Stage 2")).toBeTruthy(); // only P0 has a 2nd stage
  });

  it("offers only enabled configured channels and adds a stage", async () => {
    // Start from a clean routing so we can add a fresh stage to P3.
    getMyNotificationPreferences.mockResolvedValue({ ...basePref, routing: {} });
    render(<NotificationPreferencesPanel />);
    const addButtons = await screen.findAllByRole("button", { name: /add stage/i });
    fireEvent.click(addButtons[0]); // P0 add stage
    const channelSelect = await screen.findByLabelText("P0 stage 1 channel");
    const opts = within(channelSelect as HTMLElement)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(opts).toContain("Slack NOC");
    expect(opts).toContain("Telegram Ops");
    expect(opts).not.toContain("Disabled Discord"); // disabled connectors excluded
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

  it("saves routing as ordered stages and quiet hours with P0 bypass", async () => {
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
    expect(screen.getByText(/P0 \(Critical\) always/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() => expect(updateMyNotificationPreferences).toHaveBeenCalled());
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    // Routing is the staged shape (array of {channel_id, delay_seconds}).
    expect(Array.isArray(body.routing.P0)).toBe(true);
    expect(body.routing.P0[0]).toHaveProperty("channel_id");
    expect(body.routing.P0[0]).toHaveProperty("delay_seconds");
    expect(body.quiet_hours.min_priority_to_break).toBe("P0");
  });
});
