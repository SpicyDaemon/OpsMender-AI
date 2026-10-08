import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Mock the API surface used by the My Routing panel.
const getMyNotificationPreferences = vi.fn();
const getChannelAvailability = vi.fn();
const listBotConnectors = vi.fn();
const updateMyNotificationPreferences = vi.fn();
const testMyNotificationPreferences = vi.fn();

vi.mock("@/lib/api", () => ({
  getMyNotificationPreferences: () => getMyNotificationPreferences(),
  getChannelAvailability: () => getChannelAvailability(),
  listBotConnectors: () => listBotConnectors(),
  updateMyNotificationPreferences: (body: unknown) =>
    updateMyNotificationPreferences(body),
  testMyNotificationPreferences: () => testMyNotificationPreferences(),
}));

// Stub the embedded pages so we don't pull in their heavy dependency graph.
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

// The panel reads the current user (profile phone) for the Voice Call hint.
const mockUser: { phone: string | null } = { phone: "+14155550100" };
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: mockUser }),
}));

import {
  ChannelMultiSelect,
  NotificationPreferencesPanel,
} from "@/components/paging/PagingShell";

const basePref = {
  user_id: "u1",
  org_id: "o1",
  channels: {},
  // Legacy shape - should be read as Stage 1/2 (backward compatibility).
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
  mockUser.phone = "+14155550100";
  getMyNotificationPreferences.mockResolvedValue(basePref);
  getChannelAvailability.mockResolvedValue({ sms: false, voice: false });
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

describe("NotificationPreferencesPanel (My Routing, staged)", () => {
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

  it("explains a Voice Call stage will phone the profile number", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: { P0: [{ channel_id: "voice", delay_seconds: 0 }] },
    });
    render(<NotificationPreferencesPanel />);
    // The profile number is shown only in the per-stage Voice Call hint (the
    // footer note references "the number on your profile" generically).
    expect(await screen.findByText("+14155550100")).toBeTruthy();
  });

  it("warns when a Voice Call stage has no profile phone number", async () => {
    mockUser.phone = null;
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: { P0: [{ channel_id: "voice", delay_seconds: 0 }] },
    });
    render(<NotificationPreferencesPanel />);
    expect(
      await screen.findByText(/No phone number on your profile/i),
    ).toBeTruthy();
    mockUser.phone = "+14155550100"; // restore for other tests
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

  it("shows and saves the last stage's wait as the answer window", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: {
        P1: [
          { channel_id: "email", delay_seconds: 300 },
          { channel_id: "email", delay_seconds: 600 },
        ],
      },
    });
    render(<NotificationPreferencesPanel />);
    const firstWait = (await screen.findByLabelText("P1 stage 1 delay")) as HTMLSelectElement;
    expect(firstWait.value).toBe("300");
    const answerWindow = screen.getByLabelText("P1 answer window") as HTMLSelectElement;
    expect(answerWindow.value).toBe("600");
    expect(screen.queryByLabelText("P1 stage 2 delay")).toBeNull();
    fireEvent.change(answerWindow, { target: { value: "900" } });
    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() => expect(updateMyNotificationPreferences).toHaveBeenCalled());
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    expect(body.routing.P1.map((stage: { delay_seconds: number }) => stage.delay_seconds)).toEqual([
      300, 900,
    ]);
  });

  it("shows a stored wait that isn't one of the options", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: {
        P0: [{ channel_id: "voice", delay_seconds: 0 }],
        P2: [
          { channel_id: "email", delay_seconds: 45 },
          { channel_id: "email", delay_seconds: 3600 },
        ],
      },
    });
    render(<NotificationPreferencesPanel />);
    const shown = (label: string) => {
      const select = screen.getByLabelText(label) as HTMLSelectElement;
      return select.options[select.selectedIndex].textContent;
    };
    await screen.findByLabelText("P0 answer window");
    expect((screen.getByLabelText("P0 answer window") as HTMLSelectElement).value).toBe("0");
    expect(shown("P0 answer window")).toBe("None");
    expect(shown("P2 stage 1 delay")).toBe("45 s");
    expect(shown("P2 answer window")).toBe("60 min");
  });

  it("shows legacy routing with no answer window and saves it that way", async () => {
    // Legacy routing pages every channel at once and holds no level (O-01).
    render(<NotificationPreferencesPanel />);
    const answerWindow = (await screen.findByLabelText("P1 answer window")) as HTMLSelectElement;
    expect(answerWindow.value).toBe("0");
    expect(answerWindow.options[answerWindow.selectedIndex].textContent).toBe("None");
    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() => expect(updateMyNotificationPreferences).toHaveBeenCalled());
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    expect(body.routing.P1).toEqual([{ channel_id: "email", delay_seconds: 0 }]);
    expect(body.routing.P0.map((stage: { delay_seconds: number }) => stage.delay_seconds)).toEqual([
      0, 0,
    ]);
  });

  it("gives a new stage a 3 minute wait", async () => {
    getMyNotificationPreferences.mockResolvedValue({ ...basePref, routing: {} });
    render(<NotificationPreferencesPanel />);
    const addButtons = await screen.findAllByRole("button", { name: /add stage/i });
    fireEvent.click(addButtons[1]); // P1
    const answerWindow = (await screen.findByLabelText("P1 answer window")) as HTMLSelectElement;
    expect(answerWindow.value).toBe("180");
    expect(answerWindow.options[answerWindow.selectedIndex].textContent).toBe("3 min");
    fireEvent.click(addButtons[1]);
    expect((screen.getByLabelText("P1 stage 1 delay") as HTMLSelectElement).value).toBe("180");
  });

  it("warns when stages and answer window outlast the level's wait", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: {
        P0: [
          { channel_id: "email", delay_seconds: 600 },
          { channel_id: "email", delay_seconds: 600 },
          { channel_id: "email", delay_seconds: 600 },
        ],
        P1: [
          { channel_id: "email", delay_seconds: 180 },
          { channel_id: "email", delay_seconds: 180 },
          { channel_id: "email", delay_seconds: 180 },
        ],
        P2: [
          { channel_id: "email", delay_seconds: 300 },
          { channel_id: "email", delay_seconds: 300 },
          { channel_id: "email", delay_seconds: 300 },
        ],
      },
    });
    render(<NotificationPreferencesPanel />);
    const p0 = await screen.findByTestId("routing-ceiling-P0");
    expect(p0.textContent).toBe(
      "Your stages and answer window take longer than the 10 minutes a level " +
        "waits for you at P0: later stages run after the next person has been paged.",
    );
    // Three 3-minute stages take 9 minutes at P1; three 5-minute ones take 15
    // at P2, inside its 20.
    expect(screen.queryByTestId("routing-ceiling-P1")).toBeNull();
    expect(screen.queryByTestId("routing-ceiling-P2")).toBeNull();
    // A 5-minute answer window takes P1 to 11 minutes; every stage is sent by
    // then, only the window is cut.
    fireEvent.change(screen.getByLabelText("P1 answer window"), {
      target: { value: "300" },
    });
    expect(screen.getByTestId("routing-ceiling-P1").textContent).toBe(
      "Your stages and answer window take longer than the 10 minutes a level " +
        "waits for you at P1: the next person is paged before your answer window ends.",
    );
    // The warning does not block saving.
    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() => expect(updateMyNotificationPreferences).toHaveBeenCalled());
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    expect(body.routing.P0.map((stage: { delay_seconds: number }) => stage.delay_seconds)).toEqual([
      600, 600, 600,
    ]);
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
