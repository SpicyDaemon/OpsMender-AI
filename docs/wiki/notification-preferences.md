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

For each priority, use the **channel multi-select** (a checkbox dropdown — no Ctrl/Cmd) to pick which channels fire. Selected channels show as chips on the row. Expand a row (chevron) to set the **destination** for each selected channel:

| Channel | Destination | Notes |
|---------|-------------|-------|
| **Slack DM** | Slack user ID (e.g. `U01ABC123`) | Chat-capable — can also host incident sessions. |
| **Teams DM** | Incoming-webhook URL | Chat-capable. |
| **Email** | Email address | Delivery-only. Defaults to your account email. |
| **SMS** | E.164 phone number (e.g. `+15551234567`) | Delivery-only. |

The available channels come from the workspace's configured **Notification Channels**. If none are configured, My Routing shows an empty-state with a link to the Notification Channels tab.

If a priority has **no** channels selected, the page is recorded in `incident_pages` as `skipped` and you won't be notified.

**Chat-capable vs delivery-only:** Slack and Teams are chat-capable, so they can host an interactive incident session. Email and SMS are delivery-only — they push a notification but can't run a session.

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
| `PUT /users/me/notification-preferences` | Any user | Partial update — pass only the keys you want to change. Quiet hours use `weekday_start`/`weekday_end`, optional `days` (Mon=0..Sun=6), `time_zone`; `min_priority_to_break: "P0"` keeps P0 always paging. |
| `POST /users/me/notification-preferences/test` | Any user | Sends a one-off test notification to the caller's routed channels; returns per-channel `{channel, status, detail}`. Never fails on per-channel delivery errors. |
| `GET /organizations/{id}/notification-settings` | Admin | Returns `notification_dedup_window_minutes`. |
| `PUT /organizations/{id}/notification-settings` | Admin | Updates `notification_dedup_window_minutes` (0–1440). |
| `POST /maintenance-windows` | Admin | Accepts `description`, `scope_type` (`global`/`service`/`roster`/`team`), `scope_id`. |
| `GET /incidents/{id}/paging` | Any user | Returns `suppressed_by_maintenance_window` when applicable. |
