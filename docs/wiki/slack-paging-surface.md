# Slack as your paging surface

Sprint 36 turns Slack into a first-class paging surface for OpsMender. A paged operator can acknowledge, take over, snooze, resolve, and inspect an incident **without leaving Slack** — and click through to the web UI when they need the full session. This page is the end-to-end operator guide for that surface.

If you only want a single-line tl;dr: **page lands in your Slack DM with three buttons; you reply with `/ack` or click Acknowledge; OpsMender does the rest.**

---

## 1. What you get

When OpsMender pages you on Slack, you receive a Block Kit card in your DM with:

- The priority and status of the incident.
- The first line of the incident description.
- Three action buttons — **Acknowledge**, **Take Over**, **Resolve**.
- A **View in OpsMender** link button (when `OPSMENDER_PUBLIC_URL` is set) that deep-links to the incident detail page with a `?from=slack` breadcrumb.

Every button click is signed, verified, and authenticated against your `bot_user_links` row before any state change happens. Slack users without a link get a friendly ephemeral "your account isn't linked" message instead of a silent failure.

---

## 2. The buttons

| Button | What it does |
|--------|--------------|
| **Acknowledge** | Pauses the chain and assigns the incident to you. Same as the web "Acknowledge" button. |
| **Take Over** | Requests reassignment. If the current owner has already acked, they get a 5-minute soft-takeover window to confirm; otherwise the assignment swaps immediately. Admin-only force-takeover stays a web-only action. |
| **Resolve** | Cancels the escalation chain and marks the incident `resolved`. |
| **View in OpsMender** | Opens `/dashboard/incidents/detail?id=…&from=slack` in your browser. The detail page surfaces a "you opened this from Slack" banner so context is never lost. |

All three actions are recorded in the `incident_pages` audit log so the source surface (Slack vs. web vs. slash command) is traceable later.

---

## 3. The slash commands

Slack apps configured with the Sprint 36 slash command Request URL (`/bot/slack/commands`) expose six verbs:

| Command | Behavior |
|---------|----------|
| `/ack [incident-id]` | Acknowledge. With no id, OpsMender resolves your most recently paged active incident. |
| `/take [incident-id]` | Request take-over. |
| `/release [incident-id]` | Drop your active assignment so the chain can resume. |
| `/resolve [incident-id]` | Cancel chain and mark the incident resolved. |
| `/snooze <duration> [incident-id]` | Pause the chain and push `next_step_due_at` forward. Durations: `30m`, `2h`, `1d`. |
| `/status [incident-id]` | Without an id, lists the org's active chains. With an id, prints status / step index / next-due-at / current owner. |

A paged operator can usually just type `/ack` after receiving the DM — the implicit fallback to "your most recently paged incident" works in 95% of cases.

---

## 4. Per-incident channels (optional)

If you want every paged incident to spawn its own Slack channel, an admin can opt the org in:

```
PUT /organizations/{id}/notification-settings
{ "slack_incident_channels_enabled": true }
```

When enabled, the moment an incident enters `page` mode (REST `POST /incidents` or inbound ingest webhook), OpsMender:

1. Calls Slack's `conversations.create` with the channel name `inc-<first8hex>` (where `<first8hex>` is the leading hex of the incident UUID).
2. Stores the new channel id on `incidents.slack_channel_id` so subsequent kickoffs no-op (the mirror is idempotent).
3. Posts the same Block Kit page card to that channel.

If Slack rejects the call (`name_taken`, `missing_scope`, …), the mirror logs a warning and the chain still runs — channel mirroring is a convenience, not a hard requirement.

Required Slack app scopes:

- `channels:manage` (public channels) or `groups:write` (private channels) for the create call.
- `chat:write` for the page card.

---

## 5. Setting up the Slack app

In your Slack app's settings:

1. **Bot Token Scopes** → add `chat:write`, `users:read`, plus the channels scope above if you opt into per-incident mirroring.
2. **Event Subscriptions** → if you already wired up the bot connector for chat, leave it alone; the paging surface reuses the same connector.
3. **Interactivity & Shortcuts** → enable Interactivity, **Request URL** = `https://<your-opsmender-host>/bot/slack/interactions`.
4. **Slash Commands** → create one entry per command (`/ack`, `/take`, `/release`, `/resolve`, `/snooze`, `/status`), each pointing at `https://<your-opsmender-host>/bot/slack/commands`. The handler dispatches on the `command` field, so a single Request URL handles all six.
5. **Install** the app to the workspace. Take note of the Bot User OAuth Token and the Signing Secret.

In OpsMender:

1. **Bot Connectors** → either create a new Slack connector or edit the existing one. Paste the bot token and signing secret. Save.
2. **Bot User Links** → for every operator who should be allowed to click buttons or use slash commands, add a row mapping their Slack user id to their OpsMender user id (`POST /bot-connectors/{id}/user-links`).
3. (Optional) Set `OPSMENDER_SLACK_BOT_TOKEN` in your environment. This is the same token used by the dispatcher for DMs and by the channel mirror for `conversations.create`.

---

## 6. Verification recipe

Once the wiring is done, you can drive the full loop end-to-end without inventing a real incident:

1. Make sure your operator user has both a `bot_user_links` row and notification preferences with Slack DM enabled.
2. POST a synthetic page-mode incident: `POST /incidents` with `priority: "P0"` and a service whose chain has at least one step targeting your user.
3. You should receive a Slack DM with the page card.
4. Click **Acknowledge**. The chain should pause; `/dashboard/incidents/detail?id=…` should show you as the assignee.
5. Run `/status` in any channel. You should see your incident in the active chains list with `paused`.
6. Run `/resolve`. Incident status flips to `resolved`, the chain is `cancelled`, and the audit log records the slash-command source.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| "Your Slack account isn't linked" on every click | Missing `bot_user_links` row for that Slack user id in that connector + org. |
| 403 `invalid_signature` in OpsMender logs | The Slack `signing_secret` on the `bot_connectors` row doesn't match the app's signing secret. |
| Slash command returns "Unknown command" | The Slack app's Request URL is right, but the command name isn't one of the six supported verbs. |
| Buttons + cards show up but per-incident channels don't | `slack_incident_channels_enabled` is off on the org, or the Slack app is missing `channels:manage`. The OpsMender logs will say `slack channel mirror create failed: missing_scope`. |
| Snoozed incident never re-fires | The chain went to `paused`. `tick()` only advances `running` chains by design — un-snooze via `/take` or via the web UI to resume escalation. |

---

See also:

- [Notification Preferences](notification-preferences.md) — channels, per-priority routing, quiet hours.
- [Operator Guide](operator-guide.md) — full incident triage flow.
- `docs/paging-model.md` — the underlying data model and algorithms.
