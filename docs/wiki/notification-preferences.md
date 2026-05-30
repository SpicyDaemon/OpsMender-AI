# Set up your notification preferences

When an incident reaches OpsMender's paging engine, it has to know **how** to reach operators and **when**. The Notifications surface lives under **Paging & On-call** at `/dashboard/paging/notifications` and has four tabs:

- **My Routing** — your personal priority-based routing and quiet hours.
- **Routing Summary** — a read-only view of how incidents are routed (derived from services → escalation chains → rosters → channels). Editable team-level routing defaults are planned for v1.1.
- **Notification Channels** — the workspace delivery adapters (Slack, Teams, Telegram, Signal, WhatsApp, Discord, Mattermost, Matrix, Email, SMS, custom). Configure these once; operators route to them.
- **Viewer Notifications** — read-only/status updates to Viewer audiences and external/downstream recipients (formerly "Outbound Hooks").

Maintenance Windows remain at `/dashboard/paging/maintenance-windows`.

---

## 1. My Routing — routing by priority

Open the **My Routing** tab. Instead of a checkbox matrix, each incident priority is its own row:

| Priority | Label | Default behavior |
|----------|-------|------------------|
| **P0** | Critical | Always pages — bypasses quiet hours. |
| **P1** | High | Pages your selected channels. |
| **P2** | Medium | Notifies your selected channels. |
| **P3** | Low | Often "Do not notify". |

Each priority holds an **ordered notification escalation** of up to **3 stages**. Stage 1 fires immediately; if the incident is still unacknowledged after the stage's configured **wait** (default 5 minutes, per stage), the next stage fires, and so on:

> **P0 example**
> 1. Teams Executive Alerts → wait 5 min
> 2. SMS Primary → wait 5 min
> 3. Telegram Ops

Per stage you pick a **channel** and (for non-final stages) a **wait** before the next stage escalates. Reorder stages with the up/down controls and remove with the trash icon. **Add stage** is disabled at 3 stages.

**Escalation stops on acknowledgement or resolution.** Once you (or anyone) acknowledges or resolves the incident, no further stages are delivered.

**Channels are driven entirely by your configured Notification Channels.** Any enabled channel — Telegram, Slack, Discord, Microsoft Teams, Telegram, SMS, Email, WhatsApp, Signal, Mattermost, Matrix, and more — is selectable by its friendly name (e.g. "SMS Primary", "Slack NOC"). There is no hardcoded delivery list: add a channel in the **Notification Channels** tab and it becomes routable immediately. If no channels are configured, My Routing shows an empty state with a link to that tab.

If a priority has **no** stages, the incident does **not** notify you for that priority ("Do not notify").

**Chat-capable vs delivery-only:** Slack, Teams, Discord, Telegram, Mattermost, Matrix, and WhatsApp are chat-capable and will (in a future release) host interactive incident actions. Email and SMS are delivery-only.

Click **Test notification** (top-right) to send a one-off test to your routed channels. Channels without credentials or a destination are reported as skipped rather than failing.

---

## 2. Quiet hours

Quiet hours suppress non-critical pages during a configured window. Enable the panel and fill in:

- **Time zone** — any IANA name (`UTC`, `America/Los_Angeles`, `Europe/Berlin`).
- **Start / End** — local times. Windows wrap midnight correctly (`22:00 → 07:00`).
- **Days** — the days of week the window applies (e.g. Mon–Fri). Leave all selected for every day.

**P0 (Critical) always pages through quiet hours.** Quiet hours apply to **P1, P2, and P3 only**. When a P1–P3 page is suppressed by quiet hours, it's still recorded in `incident_pages` so the on-call audit log stays complete.

---

## 3. Dedup window

The org admin controls a `notification_dedup_window_minutes` setting under `GET/PUT /organizations/{id}/notification-settings` (default **10**). Within that window, OpsMender won't re-page the same person on the same channel for the same incident. This protects you from getting hammered by an alert that flaps.

If you find you're getting paged twice for the same thing, ask your admin to raise the window. If you find you're missing re-pages on long-running incidents, ask them to lower it.

---

## 4. Maintenance windows

Admins can schedule maintenance windows under `Paging → Maintenance Windows`:

- **Global** windows drop matching alerts for **every** service.
- **Service / Team** windows scope the drop behavior. The v1 UI supports selecting multiple services.

Inside the window:

- Matching incoming alerts are dropped at intake.
- Non-matching alerts still create incidents.

The Active / Scheduled / Past tabs let you audit what's happening now, what's coming up, and what's already passed. The From / To range filter narrows all three.

---

## 5. Verifying it works

After saving:

1. Click **Test notification** and confirm the per-channel results match what you'd expect (delivered / skipped / failed).
2. Trigger a low-stakes P3 page (e.g. via a test alert that maps to `page` mode). Confirm only the channels you selected for P3 fire.
3. Toggle quiet hours on, set start/end to **right now** with today's weekday selected, and fire a P2. Confirm it's suppressed — then fire a P0 and confirm it pages through anyway.
4. As an admin, schedule a global maintenance window covering the next 5 minutes and fire a `page` incident. Confirm no page lands. Then fire an `escalate_immediate` incident and confirm the page goes through anyway.

---

## API reference

| Endpoint | Who | What |
|----------|-----|------|
| `GET /users/me/notification-preferences` | Any user | Returns the caller's pref row; creates an empty one on first call. |
| `PUT /users/me/notification-preferences` | Any user | Partial update — pass only the keys you want to change. `routing` is `{priority: [{channel_id, delay_seconds}, ...]}` (ordered stages, max 3); legacy `{priority: ["channel_key", ...]}` is still accepted and read as stages. Quiet hours use `weekday_start`/`weekday_end`, optional `days` (Mon=0..Sun=6), `time_zone`; `min_priority_to_break: "P0"` keeps P0 always paging. |
| `POST /users/me/notification-preferences/test` | Any user | Sends a one-off test notification to the caller's routed channels; returns per-channel `{channel, status, detail}`. Never fails on per-channel delivery errors. |
| `GET /organizations/{id}/notification-settings` | Admin | Returns `notification_dedup_window_minutes`. |
| `PUT /organizations/{id}/notification-settings` | Admin | Updates `notification_dedup_window_minutes` (0–1440). |
| `POST /maintenance-windows` | Admin | Accepts `description`, `scope_type` (`global`/`service`/`roster`/`team`), `scope_id`. |
| `GET /incidents/{id}/paging` | Any user | Returns `suppressed_by_maintenance_window` when applicable. |

---

## Notification Channels (configured delivery adapters)

The **Notification Channels** tab (Admin) is where every delivery adapter is configured: Telegram, Signal, WhatsApp, Slack, Discord, Microsoft Teams, Mattermost, Matrix, Lark/Feishu, DingTalk, WeCom, WeChat, Email, SMS, Home Assistant, BlueBubbles (iMessage), and a custom adapter. Each channel has a friendly **name** (what routing displays) and its **provider/transport details live here only** — e.g. *SMS (provider: Twilio)*, *Email (provider: SMTP)*, *WhatsApp (provider: Twilio)*. Routing screens never expose provider names; they show the channel name you chose (e.g. "SMS Primary", "SMS Executive Escalation").

Adding a channel here makes it immediately routable in **My Routing** with no further changes — the routing layer routes to *configured channels*, not to platform types, so new providers never require routing changes.

---

## Future direction (not implemented yet)

> The full long-term model — Personal Operator Routing vs. Team Channels vs. Viewer Notifications, interactive incident cards, and the end-to-end incident flow — lives in [Future Incident Communication Model](../future-incident-communication.md).

The staged-routing architecture is intentionally channel-agnostic so the following can be layered on without changing routing:

- **Rich incident cards on chat-capable channels** (Slack, Teams, Discord, Telegram, Mattermost, Matrix, WhatsApp) with inline actions: **Acknowledge**, **Resolve**, **Escalate**, **Start Session**.
- Pressing an action will post an incident comment automatically:
  - Acknowledge → "Incident acknowledged by &lt;user&gt;"
  - Resolve → "Incident resolved by &lt;user&gt;"
  - Escalate → "Incident escalated by &lt;user&gt;"
  - Start Session → "Session started. Session ID: &lt;id&gt;"

These actions are **documented as direction only** and are not part of the current release. Acknowledge/resolve from the web UI already stop staged escalation today.
