/**
 * My Routing while Microsoft Teams delivery is being rebuilt (M1-96).
 *
 * A connector the API marks as sending nothing is never offered as a stage,
 * and no Teams DM choice is offered either. Saved Teams stages stay visible,
 * labelled as being rebuilt with the reason OpsMender skips them, and are
 * saved back unchanged.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
vi.mock("@/context/auth", () => ({
  useAuth: () => ({ user: { phone: "+14155550100" } }),
}));

import { NotificationPreferencesPanel } from "@/components/paging/PagingShell";

// The reason comes from the API; the UI renders whatever text it is given.
const TEAMS_REASON =
  "Microsoft Teams delivery is being rebuilt. OpsMender sends nothing to Teams until its Teams Workflows connector and bot are ready.";

const CONNECTORS = [
  {
    id: "c-slack",
    name: "Slack NOC",
    platform: "slack",
    is_enabled: true,
    platform_capabilities: { delivery: true, unavailable_reason: null },
  },
  {
    id: "c-teams",
    name: "Teams room",
    platform: "teams",
    is_enabled: true,
    platform_capabilities: { delivery: false, unavailable_reason: TEAMS_REASON },
  },
];

const basePref = {
  user_id: "u1",
  org_id: "o1",
  channels: {},
  routing: {},
  quiet_hours: null as unknown,
  updated_at: new Date().toISOString(),
};

function optionTexts(select: HTMLElement): string[] {
  return within(select)
    .getAllByRole("option")
    .map((option) => option.textContent ?? "");
}

function selectedText(label: string): string {
  const select = screen.getByLabelText(label) as HTMLSelectElement;
  return select.options[select.selectedIndex].textContent ?? "";
}

beforeEach(() => {
  vi.clearAllMocks();
  getMyNotificationPreferences.mockResolvedValue(basePref);
  getChannelAvailability.mockResolvedValue({ sms: false, voice: false });
  listBotConnectors.mockResolvedValue({
    items: CONNECTORS,
    total: CONNECTORS.length,
  });
  updateMyNotificationPreferences.mockResolvedValue(basePref);
  testMyNotificationPreferences.mockResolvedValue({ results: [], tested: 0 });
});

describe("My Routing while Teams delivery is being rebuilt", () => {
  it("offers neither a Teams connector nor a Teams DM as a new stage", async () => {
    render(<NotificationPreferencesPanel />);
    const addButtons = await screen.findAllByRole("button", {
      name: /add stage/i,
    });
    fireEvent.click(addButtons[0]);

    const select = await screen.findByLabelText("P0 stage 1 channel");
    const options = optionTexts(select as HTMLElement);
    expect(options).toContain("Slack NOC");
    expect(options.some((text) => /teams/i.test(text))).toBe(false);
    // The first offered channel is a working one.
    expect((select as HTMLSelectElement).value).toBe("c-slack");
  });

  it("shows saved Teams stages as being rebuilt and keeps them on save", async () => {
    getMyNotificationPreferences.mockResolvedValue({
      ...basePref,
      routing: {
        P0: [{ channel_id: "teams_dm", delay_seconds: 0 }],
        P1: [{ channel_id: "c-teams", delay_seconds: 0 }],
        P2: [{ channel_id: "teams_dm_graph", delay_seconds: 0 }],
      },
    });
    render(<NotificationPreferencesPanel />);

    await screen.findByLabelText("P0 answer window");
    expect(selectedText("P0 stage 1 channel")).toBe("Teams DM (being rebuilt)");
    expect(selectedText("P1 stage 1 channel")).toBe("Teams room (being rebuilt)");
    expect(selectedText("P2 stage 1 channel")).toBe("Teams DM (being rebuilt)");

    // The saved choice is shown, but no other Teams choice is offered.
    const p1Options = optionTexts(screen.getByLabelText("P1 stage 1 channel"));
    expect(p1Options.filter((text) => /teams/i.test(text))).toEqual([
      "Teams room (being rebuilt)",
    ]);

    expect(
      screen.getAllByText(
        "Teams DM is being rebuilt, so OpsMender skips this stage.",
      ),
    ).toHaveLength(2);
    expect(
      screen.getByText(`${TEAMS_REASON} OpsMender skips this stage.`),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /save routing/i }));
    await waitFor(() =>
      expect(updateMyNotificationPreferences).toHaveBeenCalled(),
    );
    const body = updateMyNotificationPreferences.mock.calls[0][0];
    expect(body.routing.P0).toEqual([{ channel_id: "teams_dm", delay_seconds: 0 }]);
    expect(body.routing.P1).toEqual([{ channel_id: "c-teams", delay_seconds: 0 }]);
    expect(body.routing.P2).toEqual([
      { channel_id: "teams_dm_graph", delay_seconds: 0 },
    ]);
  });

  it("says no channels are ready when Teams is the only connector", async () => {
    listBotConnectors.mockResolvedValue({ items: [CONNECTORS[1]], total: 1 });
    render(<NotificationPreferencesPanel />);
    expect(
      await screen.findByText(/No notification channels are configured yet/i),
    ).toBeTruthy();
    for (const button of screen.getAllByRole("button", { name: /add stage/i })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
