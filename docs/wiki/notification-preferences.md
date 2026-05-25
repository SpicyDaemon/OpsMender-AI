# Set up your notification preferences

When an incident reaches OpsMender's paging engine, it has to know **how** to reach you and **when**. This page covers the two settings surfaces that control that:

- **My Notifications** — your personal channels, per-priority routing, and quiet hours. Each user owns this.
- **Maintenance Windows** — planned downtime that suppresses paging org-wide or per service / roster / team. Admins own this.

Both live under the **Paging & On-call** sidebar group — My Notifications at `/dashboard/paging/my-notifications`, Maintenance Windows at `/dashboard/paging/maintenance-windows`.

---

## 1. Channels

Open `/dashboard/paging/my-notifications`. Pick which channels OpsMender can reach you on:

| Channel | Destination field | Notes |
|---------|-------------------|-------|
| **Slack DM** | Slack user ID (e.g. `U01ABC123`) | Sent via the org's Slack bot token. |
| **Teams DM** | Incoming-webhook URL | Posts to a channel until full Graph DMs land in Sprint 37. |
| **Email** | Email address | Delivered via the org's configured SMTP. |
| **SMS** | E.164 phone number (e.g. `+15551234567`) | Twilio under the hood. |

Toggling a channel on reveals the destination input. Toggling it off removes it from every priority row in the routing matrix as well, so you can never page yourself on an unconfigured channel.

---

## 2. Per-priority routing matrix

Below the channels section, you'll see a 4×4 grid: priorities **P0 / P1 / P2 / P3** on the rows, your enabled channels on the columns. Tick the cells that should fire for each priority.

The cells are **disabled** until the channel is enabled above — that's the only place destination addresses are stored.

Typical setups:

- **Heavy P0 / quiet P3** — P0 → Slack + SMS, P1 → Slack + Email, P2/P3 → Email only.
- **All-hands paging** — Every priority routes to Slack DM. Useful for solo operators.
- **Email-only** — Every priority routes to Email. Useful for triage backstops where SMS cost matters.

If a priority has **no** channels checked, the page is recorded in `incident_pages` as `skipped` and you won't be notified.

---

## 3. Quiet hours

Quiet hours block low-priority pages during a configured window. Enable the panel and fill in:

- **Start / End** — local times in the time zone below.
- **Time zone** — any IANA name (`UTC`, `America/Los_Angeles`, `Europe/Berlin`).
- **Break for priority ≥** — the threshold that still pages through. `P1` and higher means P0/P1 break through, P2/P3 are suppressed. **Never break** means even P0 waits until the window ends.

Windows wrap midnight correctly (`22:00 → 07:00` is the obvious one).

When a page is suppressed by quiet hours, it's still recorded in `incident_pages` so the on-call audit log stays complete — you just won't see a Slack DM until quiet hours lift.

---

## 4. Dedup window

The org admin controls a `notification_dedup_window_minutes` setting under `GET/PUT /organizations/{id}/notification-settings` (default **10**). Within that window, OpsMender won't re-page the same person on the same channel for the same incident. This protects you from getting hammered by an alert that flaps.

If you find you're getting paged twice for the same thing, ask your admin to raise the window. If you find you're missing re-pages on long-running incidents, ask them to lower it.

---

## 5. Maintenance windows

Admins can schedule maintenance windows under `Paging → Maintenance Windows`:

- **Global** windows suppress paging for **every** service.
- **Service / Roster / Team** windows scope the suppression. Pick the target from the dropdown.

Inside the window:

- `response_mode = page` incidents are suppressed entirely — no channels fire.
- `response_mode = escalate_immediate` incidents **still page through**. Maintenance windows never silence critical alerts.

When an incident is suppressed, you'll see an amber **"Paging suppressed by maintenance window"** banner at the top of the incident detail page, including the window name, scope, and time range.

The Active / Scheduled / Past tabs let you audit what's happening now, what's coming up, and what's already passed. The From / To range filter narrows all three.

---

## 6. Verifying it works

After saving:

1. Trigger a low-stakes P3 page (e.g. via a test alert that maps to `page` mode). Confirm only the channels you ticked fire.
2. Set the test incident's priority to P0. Confirm channels below the routing matrix's P0 row also fire.
3. Toggle quiet hours on, set start/end to **right now**, and fire another P3. Confirm it's suppressed.
4. As an admin, schedule a global maintenance window covering the next 5 minutes and fire a `page` incident. Confirm the amber banner shows on the incident detail and that no Slack DM lands. Then fire an `escalate_immediate` incident and confirm the page goes through anyway.

---

## API reference

| Endpoint | Who | What |
|----------|-----|------|
| `GET /users/me/notification-preferences` | Any user | Returns the caller's pref row; creates an empty one on first call. |
| `PUT /users/me/notification-preferences` | Any user | Partial update — pass only the keys you want to change. |
| `GET /organizations/{id}/notification-settings` | Admin | Returns `notification_dedup_window_minutes`. |
| `PUT /organizations/{id}/notification-settings` | Admin | Updates `notification_dedup_window_minutes` (0–1440). |
| `POST /maintenance-windows` | Admin | Accepts `description`, `scope_type` (`global`/`service`/`roster`/`team`), `scope_id`. |
| `GET /incidents/{id}/paging` | Any user | Returns `suppressed_by_maintenance_window` when applicable. |
