/**
 * Notification Channels — honest capability rendering.
 *
 * The table must advertise only what each platform can actually do: a
 * delivery-only channel (Twilio SMS) shows "Delivery-only" and never an
 * "Actions" chip, while a chat-capable channel (Slack) shows "Incident cards".
 * Platform names render in their friendly form, including "Twilio (SMS)".
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

// The component pulls in the whole config API surface; only listBotPlatformSchemas
// runs on mount. Stub the module so the import graph resolves.
vi.mock("@/lib/api", () => ({
  listBotPlatformSchemas: () => Promise.resolve({ items: [], total: 0 }),
}));

import { BotConnectorSection } from "@/components/config/ConfigSections";
import type { BotConnectorResponse, PlatformCapabilities } from "@/lib/types";

function caps(overrides: Partial<PlatformCapabilities>): PlatformCapabilities {
  return {
    platform: "x",
    display_name: "X",
    delivery: true,
    incident_card: false,
    interactive_actions: false,
    direct_message: false,
    shared_channel: false,
    ai_session_link: true,
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
    expect(within(row).queryByText("Actions")).toBeNull();
  });

  it("delivery-only channel shows Delivery-only and never Actions", () => {
    render(
      <BotConnectorSection connectors={[sms]} onReload={async () => {}} canEdit />,
    );
    const row = screen.getByText("On-call SMS").closest("tr")!;
    expect(within(row).getByText("Delivery-only")).toBeTruthy();
    expect(within(row).queryByText("Actions")).toBeNull();
    expect(within(row).queryByText("Incident updates")).toBeNull();
  });
});
