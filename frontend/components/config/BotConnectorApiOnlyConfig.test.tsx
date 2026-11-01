/**
 * M1-120 (L-06): editing a Notification Channel in the dashboard keeps the
 * settings that have no field in the form.
 *
 * Telegram's allowed chats and rate limit are set through the API only. The
 * form is built from the platform schema, so a save used to send only the
 * visible fields and the API replaced the saved config with that body: an
 * ordinary edit (a new bot token, say) silently dropped the chat restriction
 * and every chat could use the bot. The dialog now carries the saved keys it
 * has no field for, says which ones it keeps, and still lets a visible field
 * be cleared on purpose.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const platformSchemas = vi.hoisted(() => [
  {
    platform: "telegram",
    label: "Telegram",
    oauth_enabled: false,
    capabilities: {
      platform: "telegram",
      display_name: "Telegram",
      delivery: true,
      incident_card: true,
      incident_updates: true,
      interactive_actions: false,
      direct_message: true,
      shared_channel: true,
      ai_session_link: true,
      message_update: false,
      interaction_route: null,
      delivery_only: false,
    },
    fields: [
      {
        name: "bot_token",
        label: "Bot token",
        kind: "secret",
        group: "credentials",
        required: true,
        helper: null,
        doc_url: null,
        placeholder: null,
        default: null,
        options: [],
      },
      {
        name: "webhook_secret",
        label: "Webhook secret token",
        kind: "secret",
        group: "credentials",
        required: true,
        helper: null,
        doc_url: null,
        placeholder: null,
        default: null,
        options: [],
      },
      {
        name: "default_chat_id",
        label: "Default chat ID",
        kind: "text",
        group: "config",
        required: false,
        helper: null,
        doc_url: null,
        placeholder: null,
        default: null,
        options: [],
      },
    ],
  },
]);

const createBotConnectorMock = vi.hoisted(() => vi.fn());
const updateBotConnectorMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  listBotPlatformSchemas: () =>
    Promise.resolve({ items: platformSchemas, total: platformSchemas.length }),
  testBotConnector: vi.fn(),
  createBotConnector: createBotConnectorMock,
  updateBotConnector: updateBotConnectorMock,
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
      ],
      total: 1,
    }),
}));

import { BotConnectorSection } from "@/components/config/ConfigSections";
import type { BotConnectorResponse } from "@/lib/types";

// A Telegram channel as the API returns it after an admin restricted it to
// one group and lowered its rate limit through PUT /bot-connectors/{id}.
function telegram(config: Record<string, unknown> | null): BotConnectorResponse {
  return {
    id: "c-telegram",
    name: "Telegram on-call group",
    platform: "telegram",
    config,
    allowed_capabilities: ["incident_lookup", "notifications"],
    lanes: ["respond"],
    status: "configured",
    is_enabled: true,
    created_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:00:00Z",
    last_checked_at: null,
    last_error: null,
    credential_keys: ["bot_token", "webhook_secret"],
    has_credentials: true,
    team_scope: "workspace",
    team_ids: [],
    team_names: [],
    platform_label: "Telegram",
    platform_capabilities: platformSchemas[0].capabilities,
  } as BotConnectorResponse;
}

const restricted = telegram({
  default_chat_id: "-1001234567890",
  allowed_chat_ids: ["-1001234567890"],
  rate_limit_per_minute: 5,
});

async function openEdit(saved: BotConnectorResponse) {
  updateBotConnectorMock.mockReset();
  updateBotConnectorMock.mockResolvedValue(saved);
  render(
    <BotConnectorSection connectors={[saved]} onReload={async () => {}} canEdit />,
  );
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: /^Edit$/i }));
  return screen.findByRole("dialog");
}

async function savedConfig(dialog: HTMLElement) {
  const save = within(dialog).getByRole("button", { name: /Save Changes/i });
  fireEvent.submit(save.closest("form")!);
  await waitFor(() => expect(updateBotConnectorMock).toHaveBeenCalled());
  const [id, payload] = updateBotConnectorMock.mock.calls[0];
  expect(id).toBe("c-telegram");
  return payload;
}

describe("Notification Channel edits keep API-only settings (M1-120)", () => {
  it("keeps the chat restriction and rate limit when the bot token is replaced", async () => {
    const dialog = await openEdit(restricted);

    // The round's reproduction: replace the credentials, change nothing else.
    fireEvent.click(within(dialog).getByRole("button", { name: /^Replace$/i }));
    fireEvent.change(within(dialog).getByLabelText(/Bot token/i), {
      target: { value: "123456:new-token" },
    });
    fireEvent.change(within(dialog).getByLabelText(/Webhook secret token/i), {
      target: { value: "new-secret" },
    });

    const payload = await savedConfig(dialog);
    expect(payload.config).toEqual({
      default_chat_id: "-1001234567890",
      allowed_chat_ids: ["-1001234567890"],
      rate_limit_per_minute: 5,
    });
    expect(payload.credentials).toEqual({
      bot_token: "123456:new-token",
      webhook_secret: "new-secret",
    });
  });

  it("still clears a visible field on purpose while keeping the hidden ones", async () => {
    const dialog = await openEdit(restricted);

    fireEvent.change(within(dialog).getByLabelText(/Default chat ID/i), {
      target: { value: "" },
    });

    const payload = await savedConfig(dialog);
    expect(payload.config).toEqual({
      allowed_chat_ids: ["-1001234567890"],
      rate_limit_per_minute: 5,
    });
    expect(payload.credentials).toBeUndefined();
    expect(payload.clear_credentials).toBeUndefined();
  });

  it("does not carry team scope inside config; the scope fields own it", async () => {
    const scoped = {
      ...telegram({
        allowed_chat_ids: ["-1001234567890"],
        team_scope: "teams",
        team_ids: ["team-1"],
      }),
      team_scope: "teams" as const,
      team_ids: ["team-1"],
      team_names: ["Platform"],
    };
    const dialog = await openEdit(scoped);

    fireEvent.click(within(dialog).getByLabelText(/Workspace-wide/i));

    const payload = await savedConfig(dialog);
    expect(payload.team_scope).toBe("workspace");
    expect(payload.team_ids).toEqual([]);
    expect(payload.config).toEqual({ allowed_chat_ids: ["-1001234567890"] });
  });

  it("tells the admin which saved settings the form keeps", async () => {
    const dialog = await openEdit(restricted);
    const note = within(dialog).getByText(/Kept as saved/i);
    expect(note.textContent).toContain("allowed_chat_ids");
    expect(note.textContent).toContain("rate_limit_per_minute");
    expect(note.textContent).not.toContain("default_chat_id");
  });

  it("shows no such note for a channel whose settings all have fields", async () => {
    const dialog = await openEdit(telegram({ default_chat_id: "-100" }));
    expect(within(dialog).queryByText(/Kept as saved/i)).toBeNull();
    const payload = await savedConfig(dialog);
    expect(payload.config).toEqual({ default_chat_id: "-100" });
  });
});
