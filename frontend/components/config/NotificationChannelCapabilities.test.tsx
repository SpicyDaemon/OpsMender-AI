/**
 * Notification Channels — honest capability rendering.
 *
 * The table must advertise only what each platform can actually do: a
 * delivery-only channel (Twilio SMS) shows "Delivery-only" and never an
 * "Interactive actions" chip, while a chat-capable channel (Slack) shows
 * "Incident updates".
 * Platform names render in their friendly form, including "Twilio (SMS)".
 */

import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// The component pulls in the whole config API surface; only listBotPlatformSchemas
// runs on mount. Stub the module so the import graph resolves.
vi.mock("@/lib/api", () => ({
  listBotPlatformSchemas: () => Promise.resolve({ items: [], total: 0 }),
  listTeams: () =>
    Promise.resolve({
      items: [
        {
          id: "team-1",
          name: "Platform",
          slug: "platform",
          description: null,
          created_at: "2026-06-05T00:00:00Z",
          updated_at: "2026-06-05T00:00:00Z",
        },
        {
          id: "team-2",
          name: "Payments",
          slug: "payments",
          description: null,
          created_at: "2026-06-05T00:00:00Z",
          updated_at: "2026-06-05T00:00:00Z",
        },
      ],
      total: 2,
    }),
}));

import { BotConnectorSection } from "@/components/config/ConfigSections";
import type { BotConnectorResponse, PlatformCapabilities } from "@/lib/types";

function caps(overrides: Partial<PlatformCapabilities>): PlatformCapabilities {
  return {
    platform: "x",
    display_name: "X",
    delivery: true,
    incident_card: false,
    incident_updates: true,
    interactive_actions: false,
    direct_message: false,
    shared_channel: false,
    ai_session_link: true,
    message_update: false,
    delivery_only: true,
    ...overrides,
  };
}

function connector(
  over: Partial<BotConnectorResponse> & Pick<BotConnectorResponse, "id" | "name" | "platform">,
): BotConnectorResponse {
  return {
    config: null,
    allowed_capabilities: ["notifications"],
    status: "configured",
    is_enabled: true,
    created_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:00:00Z",
    last_checked_at: null,
    last_error: null,
    credential_keys: [],
    has_credentials: false,
    team_scope: "workspace",
    team_ids: [],
    team_names: [],
    platform_label: null,
    platform_capabilities: null,
    ...over,
  } as BotConnectorResponse;
}

const slack = connector({
  id: "c1",
  name: "Slack #incidents",
  platform: "slack",
  platform_label: "Slack",
  platform_capabilities: caps({
    platform: "slack",
    display_name: "Slack",
    incident_card: true,
    shared_channel: true,
    delivery_only: false,
  }),
});

const sms = connector({
  id: "c2",
  name: "On-call SMS",
  platform: "twilio",
  platform_label: "Twilio (SMS)",
  platform_capabilities: caps({
    platform: "twilio",
    display_name: "Twilio (SMS)",
    direct_message: true,
  }),
});

describe("Notification Channels capability rendering", () => {
  it("renders friendly platform names including Twilio (SMS)", () => {
    render(
      <BotConnectorSection connectors={[slack, sms]} onReload={async () => {}} canEdit />,
    );
    expect(screen.getAllByText("Twilio (SMS)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Slack").length).toBeGreaterThan(0);
  });

  it("shows Incident updates (not buttons) for a chat-capable channel", () => {
    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    const row = screen.getByText("Slack #incidents").closest("tr")!;
    // v1 label is "Incident updates" — must not imply in-chat action buttons.
    expect(within(row).getByText("Incident updates")).toBeTruthy();
    expect(within(row).queryByText("Incident cards")).toBeNull();
    // No interactive actions are advertised in v1.
    expect(within(row).queryByText("Interactive actions")).toBeNull();
    expect(within(row).queryByText("Message updates")).toBeNull();
  });

  it("delivery-only channel shows Delivery-only and never Interactive actions", () => {
    render(
      <BotConnectorSection connectors={[sms]} onReload={async () => {}} canEdit />,
    );
    const row = screen.getByText("On-call SMS").closest("tr")!;
    expect(within(row).getByText("Delivery-only")).toBeTruthy();
    expect(within(row).queryByText("Interactive actions")).toBeNull();
  });

  it("shows native action and message update chips only when supported", () => {
    const futurePlatform = connector({
      id: "c3",
      name: "Future chat",
      platform: "slack",
      platform_label: "Future Chat",
      platform_capabilities: caps({
        platform: "slack",
        display_name: "Future Chat",
        incident_card: true,
        interactive_actions: true,
        message_update: true,
        shared_channel: true,
        delivery_only: false,
      }),
    });
    render(
      <BotConnectorSection connectors={[futurePlatform]} onReload={async () => {}} canEdit />,
    );
    const row = screen.getByText("Future chat").closest("tr")!;
    expect(within(row).getByText("Incident updates")).toBeTruthy();
    expect(within(row).getByText("Message updates")).toBeTruthy();
    expect(within(row).getByText("Interactive actions")).toBeTruthy();
  });

  it("shows team scope and defaults old channels to workspace-wide", () => {
    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    expect(screen.getByText("Team Scope")).toBeTruthy();
    const row = screen.getByText("Slack #incidents").closest("tr")!;
    expect(within(row).getByText("Workspace-wide")).toBeTruthy();
  });

  it("lets admins choose specific teams in the modal", async () => {
    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: /add channel/i }));
    expect(screen.getAllByText("Team Scope").length).toBeGreaterThan(1);
    expect(screen.getByLabelText("Workspace-wide")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Specific teams"));

    await waitFor(() => expect(screen.getByText("Payments")).toBeTruthy());
    const payments = screen
      .getByText("Payments")
      .closest("label")!
      .querySelector("input") as HTMLInputElement;
    fireEvent.click(payments);
    expect(payments.checked).toBe(true);
  });
});
