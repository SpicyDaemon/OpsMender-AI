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

// The Platform picker is a custom IconSelect (button + listbox), not a native
// <select>: open it and click the option carrying the target data-value.
function pickPlatform(value: string) {
  fireEvent.click(screen.getByLabelText("Platform"));
  const option = document.querySelector(`[role="option"][data-value="${value}"]`);
  if (!option) throw new Error(`Platform option not found: ${value}`);
  fireEvent.click(option);
}

const platformSchemas = vi.hoisted(() => [
  {
    platform: "discord",
    label: "Discord",
    oauth_enabled: false,
    capabilities: {
      platform: "discord",
      display_name: "Discord",
      delivery: true,
      incident_card: true,
      incident_updates: true,
      interactive_actions: true,
      direct_message: false,
      shared_channel: true,
      ai_session_link: true,
      message_update: true,
      interaction_route: "/bot-connectors/{connector_id}/discord/webhook",
      delivery_only: false,
    },
    fields: [
      {
        name: "public_key",
        label: "Application public key",
        kind: "secret",
        group: "credentials",
        required: true,
        default: null,
        helper: "From your Discord application.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "bot_token",
        label: "Bot token",
        kind: "secret",
        group: "credentials",
        required: true,
        default: null,
        helper: "Required for outbound message delivery.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "default_chat_id",
        label: "Discord Channel ID",
        kind: "text",
        group: "config",
        required: false,
        default: null,
        helper:
          "Optional. The Discord channel ID where OpsMender should post outbound notifications. In Discord, enable Developer Mode, right-click the channel, and copy its ID.",
        doc_url: null,
        placeholder: "123456789012345678",
        options: [],
      },
    ],
  },
  {
    platform: "email",
    label: "Mailgun Email",
    oauth_enabled: false,
    capabilities: null,
    fields: [
      {
        name: "mailgun_api_key",
        label: "Mailgun API key",
        kind: "secret",
        group: "credentials",
        required: true,
        default: null,
        helper: "Used for Mailgun delivery and webhook verification.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "mailgun_domain",
        label: "Mailgun sending domain",
        kind: "text",
        group: "credentials",
        required: true,
        default: null,
        helper: "Domain configured in Mailgun.",
        doc_url: null,
        placeholder: "mg.example.com",
        options: [],
      },
      {
        name: "from_email",
        label: "From address",
        kind: "text",
        group: "credentials",
        required: false,
        default: null,
        helper: "Optional sender address.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "default_chat_id",
        label: "Default recipient",
        kind: "text",
        group: "config",
        required: false,
        default: null,
        helper: "Optional recipient email used for outbound notifications.",
        doc_url: null,
        placeholder: "oncall@example.com",
        options: [],
      },
    ],
  },
  {
    platform: "smtp",
    label: "SMTP Email",
    oauth_enabled: false,
    capabilities: null,
    fields: [
      {
        name: "smtp_host",
        label: "SMTP host",
        kind: "text",
        group: "credentials",
        required: true,
        default: null,
        helper: "Hosted provider or infrastructure mail server.",
        doc_url: null,
        placeholder: "smtp.example.com",
        options: [],
      },
      {
        name: "smtp_port",
        label: "SMTP port",
        kind: "text",
        group: "credentials",
        required: true,
        default: "587",
        helper: "Usually 587 for STARTTLS.",
        doc_url: null,
        placeholder: "587",
        options: [],
      },
      {
        name: "security",
        label: "Connection security",
        kind: "select",
        group: "credentials",
        required: true,
        default: "starttls",
        helper: null,
        doc_url: null,
        placeholder: null,
        options: [
          { value: "starttls", label: "STARTTLS" },
          { value: "ssl", label: "Implicit TLS / SSL" },
          { value: "none", label: "None (trusted internal relay only)" },
        ],
      },
      {
        name: "smtp_username",
        label: "SMTP username",
        kind: "text",
        group: "credentials",
        required: false,
        default: null,
        helper: "Optional for trusted relays.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "smtp_password",
        label: "SMTP password",
        kind: "secret",
        group: "credentials",
        required: false,
        default: null,
        helper: "Use a provider SMTP credential where supported.",
        doc_url: null,
        placeholder: null,
        options: [],
      },
      {
        name: "from_email",
        label: "From address",
        kind: "text",
        group: "credentials",
        required: true,
        default: null,
        helper: null,
        doc_url: null,
        placeholder: "opsmender@example.com",
        options: [],
      },
      {
        name: "default_chat_id",
        label: "Default recipient",
        kind: "text",
        group: "config",
        required: false,
        default: null,
        helper: "Optional recipient email used for outbound notifications.",
        doc_url: null,
        placeholder: "oncall@example.com",
        options: [],
      },
    ],
  },
]);

// The component pulls in the whole config API surface; only listBotPlatformSchemas
// runs on mount. Stub the module so the import graph resolves.
const testBotConnectorMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  listBotPlatformSchemas: () =>
    Promise.resolve({ items: platformSchemas, total: platformSchemas.length }),
  testBotConnector: testBotConnectorMock,
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
    interaction_route: null,
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
    lanes: ["respond"],
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

const teamsNative = connector({
  id: "c-teams",
  name: "Teams incident room",
  platform: "teams",
  platform_label: "Microsoft Teams",
  platform_capabilities: caps({
    platform: "teams",
    display_name: "Microsoft Teams",
    incident_card: true,
    interactive_actions: true,
    shared_channel: true,
    delivery_only: false,
  }),
  native_actions_enabled: true,
  callback_status: "configured",
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
    // The base Slack channel has not opted into native actions.
    expect(within(row).getByText("Incident updates")).toBeTruthy();
    expect(within(row).queryByText("Incident cards")).toBeNull();
    // No interactive actions are advertised until channel readiness is enabled.
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
      native_actions_enabled: true,
      callback_status: "configured",
    });
    render(
      <BotConnectorSection connectors={[futurePlatform]} onReload={async () => {}} canEdit />,
    );
    const row = screen.getByText("Future chat").closest("tr")!;
    expect(within(row).getByText("Incident updates")).toBeTruthy();
    expect(within(row).getByText("Message updates")).toBeTruthy();
    expect(within(row).getByText("Interactive actions")).toBeTruthy();
  });

  it("shows configured Teams native actions when the channel opts in", () => {
    render(
      <BotConnectorSection
        connectors={[teamsNative]}
        onReload={async () => {}}
        canEdit
      />,
    );
    const row = screen.getByText("Teams incident room").closest("tr")!;
    expect(within(row).getByText("Interactive actions")).toBeTruthy();
    expect(within(row).getByText(/Teams callbacks:/)).toBeTruthy();
    expect(within(row).getByText("configured")).toBeTruthy();
  });

  it("shows team scope and defaults old channels to workspace-wide", () => {
    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    expect(screen.getByText("Team Scope")).toBeTruthy();
    const row = screen.getByText("Slack #incidents").closest("tr")!;
    expect(within(row).getByText("Workspace-wide")).toBeTruthy();
  });

  it("shows lane tags and lets admins enable Track for Slack", async () => {
    const trackedSlack = connector({
      ...slack,
      id: "c-track",
      name: "Slack status",
      lanes: ["respond", "track"],
    });
    render(
      <BotConnectorSection
        connectors={[trackedSlack]}
        onReload={async () => {}}
        canEdit
      />,
    );
    const row = screen.getByText("Slack status").closest("tr")!;
    expect(within(row).getByText("Respond")).toBeTruthy();
    expect(within(row).getByText("Track")).toBeTruthy();

    fireEvent.click(within(row).getByRole("button", { name: /edit/i }));
    expect((screen.getByLabelText(/Track/) as HTMLInputElement).checked).toBe(true);
  });

  it("lets admins choose specific teams in the modal", async () => {
    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: /new channel/i }));
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

  it("offers config-check and live-test buttons and renders structured checks", async () => {
    testBotConnectorMock.mockReset();
    testBotConnectorMock.mockResolvedValue({
      success: true,
      detail: "Live test message delivered; configuration looks healthy.",
      status: "healthy",
      live_message_sent: true,
      target_chat_id: "C123",
      checks: [
        { name: "enabled", level: "pass", detail: "Connector is enabled." },
        {
          name: "destination",
          level: "warn",
          detail: "No destination chat configured; lifecycle posts will be skipped.",
        },
        { name: "delivery", level: "pass", detail: "Test message delivered to C123." },
      ],
    });

    render(
      <BotConnectorSection connectors={[slack]} onReload={async () => {}} canEdit />,
    );
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: /^Edit$/i }));
    expect(
      await screen.findByRole("button", { name: /Check configuration/i }),
    ).toBeTruthy();
    const liveButton = screen.getByRole("button", { name: /Send live test/i });
    fireEvent.click(liveButton);

    await waitFor(() =>
      expect(testBotConnectorMock).toHaveBeenCalledWith("c1", { live: true }),
    );
    expect(await screen.findByText(/live message sent/i)).toBeTruthy();
    expect(screen.getByText(/Test message delivered to C123\./)).toBeTruthy();
    expect(
      screen.getByText(/No destination chat configured/),
    ).toBeTruthy();
  });

  it("uses accurate Discord and Mailgun configuration copy", async () => {
    render(
      <BotConnectorSection connectors={[]} onReload={async () => {}} canEdit />,
    );
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: /new channel/i }));

    pickPlatform("discord");
    expect(await screen.findByLabelText(/Discord Channel ID/i)).toBeTruthy();
    expect(
      screen.getByText(/Enable verified Discord actions/i),
    ).toBeTruthy();
    expect(screen.getByText(/Discord verifies with Ed25519/i)).toBeTruthy();
    expect(screen.getByLabelText(/Track/) as HTMLInputElement).toBeTruthy();
    expect(
      screen.getByText(
        /The Discord channel ID where OpsMender should post outbound notifications/i,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Snowflake/i)).toBeNull();

    pickPlatform("email");
    expect(await screen.findByText("Mailgun Email can:")).toBeTruthy();
    expect(screen.getByLabelText(/Mailgun API key/i)).toBeTruthy();
    expect(screen.getByLabelText(/Mailgun sending domain/i)).toBeTruthy();
    expect(screen.getByLabelText(/From address/i)).toBeTruthy();
    expect(screen.getByLabelText(/Default recipient/i)).toBeTruthy();
    expect(screen.queryByText(/IMAP/i)).toBeNull();
    expect(screen.getByText("Enable this notification channel")).toBeTruthy();

    // "smtp" is retired as a notification channel — SMTP is now the single
    // workspace setting under Config → Email / SMTP, so it is not offered as a
    // creatable connector platform.
    fireEvent.click(screen.getByLabelText("Platform"));
    expect(
      document.querySelector('[role="option"][data-value="smtp"]'),
    ).toBeNull();
  });
});
