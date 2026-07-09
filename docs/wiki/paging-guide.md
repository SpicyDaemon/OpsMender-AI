# Paging Guide

This is the v1 guide to OpsMender's paging and on-call surface. The product model is intentionally small:

- Services receive alerts and own incident priority.
- Teams own services and escalation chains.
- Rosters define who is on call for coverage windows.
- Maintenance windows drop matching alerts during planned work.
- Notifications covers operator delivery, quiet hours, routing by priority, and
  chat sessions. Stakeholder updates are delivered through incident reports.

If you only want a one-line summary: create a Team, create its Escalation Chain, add a Service with an intake endpoint, attach a Roster schedule, then configure Notifications.

For the data-model + algorithm overview (Paging Model, design invariant D-021), see `backend/paging/on_call.py`. For platform-specific chat details, see [Slack as your paging surface](slack-paging-surface.md) and [Teams as your paging surface](teams-paging-surface.md).

---

## 1. The Incident-Response Loop

Every paged incident walks the same simple loop:

1. Alert fires and POSTs to a service endpoint: `POST /api/v1/intake/{service_token}`.
2. The service sets the priority: `P0 Critical`, `P1 High`, `P2 Medium`, or `P3 Low`.
3. If an active maintenance window matches the service or team scope, the alert is dropped and no visible incident is created.
4. OpsMender creates the incident and starts the AI session.
5. The team's escalation chain pages the roster or user levels for that service.
6. An operator acknowledges, works the incident, and resolves it with or without AI assistance.

The AI does not assign or override priority in v1. A service's MCP servers are
a strict allowlist: sessions for that service can only use those servers, and
an empty list means the service's sessions have no MCP access.

---

## 2. Conceptual Model

```
Organization
  └── Teams
       ├── Escalation Chains
       └── Services
            ├── Generated intake URL
            ├── Fixed priority
            ├── MCP servers
            ├── Rosters
            └── Maintenance Windows

Users
  └── Notifications
       ├── My Routing
       ├── Quiet Hours
       ├── Routing by Priority
       └── Sessions / Chat

Reports
  ├── On-demand CSV / PDF
  └── Scheduled stakeholder email
```

Roles:

- Admin manages workspace, services, teams, rosters, escalation chains, notifications, and AI configuration.
- Operator can participate in rosters, receive on-call notifications, work incidents, and use AI assistance.
- Viewer is a read-only/status recipient and cannot be assigned to on-call rosters.

---

## 3. Services

Services are the canonical alert intake surface. Each service represents an alert source or monitored area that can create incidents through a generated endpoint:

```http
POST /api/v1/intake/{service_token}
```

The token is an embedded unguessable secret in the URL. External monitors can POST alerts directly without managing separate API-key headers.

Each service has:

- Name and slug.
- Owning team.
- Fixed priority: `P0`, `P1`, `P2`, or `P3`.
- Enabled state.
- Generated intake URL.
- MCP servers strict allowlist.
- Up to three ranked Models.

MCP servers define the only configured MCP servers sessions for that service may
use. Leave the list empty to give the service's sessions no MCP tools.

Models define the service-specific models an operator may switch to during an
AI session. The workspace default model is always available, and a service with
no Models configured exposes only that default in the session picker. Automatic
incident/session model selection still uses the existing fallback chain.

Manual incidents created from the Incidents page must be linked to an active
service so ownership, routing, escalation, MCP, and model preferences are
unambiguous.

### Similar Alert Grouping & Flapping

Similar alert grouping is an optional noise-control setting on each Service:

- `Inherit` uses the workspace default from Settings.
- `On` groups similar inbound alerts into the active Incident for that Service.
- `Off` keeps every distinct inbound alert on the normal creation path.

When grouping is on, OpsMender compares alert titles within a short automatic
window. Similar alerts on the same Service update the existing Incident, add a
system timeline comment, increment the grouped count, and do not page or
auto-start a Session again. Similar alerts on different Services never group.

Flapping detection uses the same Service setting. Repeated fire/clear
transitions for the same alert fingerprint mark the Incident as flapping and
suppress re-pages for a short automatic period. The thresholds are intentionally
built in for v1; there are no tuning fields to maintain.

P0 is the safety valve: flapping suppression never blocks a P0 re-fire. If a
Service is P0, the re-fire creates and pages normally even while the fingerprint
is in its suppression period. After the suppression period lapses, non-P0
re-fires return to the normal grouping/create path.

---

## 4. Rosters

A roster is a schedule. It defines who is on call for a specific coverage window and rotation pattern.

Roster fields in v1:

- Team.
- Name.
- Enabled or disabled.
- Time zone.
- Start Date.
- Coverage window start and end time, including overnight windows like `18:00 -> 08:00`.
- Rotation frequency: daily, weekly, or custom number of days.
- Ordered users. Admin and Operator users can be included; Viewer users cannot.

Example:

- `DevOps-Day`: `08:00 -> 18:00`, daily rotation, Start Date is the first day user 1 is on call.
- `DevOps-Night`: `18:00 -> 08:00`, daily rotation, same ordered user behavior across the overnight window.

The calendar view resolves who is on call for current and near-future windows from the roster's coverage window and rotation order.

---

## 5. Escalation Chains

Every escalation chain belongs to a team. Chains define levels:

- Level 1: the main operator or roster.
- Level 2: a supervisor, backup user, or backup roster.
- Later levels are optional.

Services use their team's escalation behavior. Roster and user targets are supported in v1. Team-to-team fallback is a later enhancement.

The Escalation Chain Calendar shows who is expected to respond at each escalation level over a selected time range. It is resolved from chain levels, roster schedules, rotation order, coverage windows, and active users.

---

## 6. On Call Schedule

The On Call Schedule (`Paging & On-call → On Call Schedule`) is the team-level
month calendar. Everyone can view it (read-only); **calendar changes are
admin-only**.

Each day cell shows the **full coverage picture**: every escalation chain of
the selected team as a caption with its **Level 1 / 2 / 3 chips**. Each chip
shows the level, the on-call person (color-coded, consistent across days), and
that level's **shift window in the roster's own time zone** — for example
`L1 · on-call · 09:00–17:00 EDT`. Days under a maintenance window show a
maintenance marker.

Use the **Show times in** dropdown (left of the month navigation) to re-express
every shift in a time zone of your choosing — rosters store their own zone
(usually UTC) and the calendar converts on the fly (a `09:00–17:00 UTC` shift
reads `04:00–12:00 CDT` when viewed in US Central), with a ⁺¹/⁻¹ marker when a
converted time crosses midnight. This only changes what you see, not the
schedule. Every time-zone dropdown in the app also shows the zone's current UTC
offset, e.g. `America/Chicago (-05:00)`.

Interactions (admin):

- **Click a person (chip)** to replace who's on call for that level. The
  "Replace who's on call" dialog shows the chain, level, roster, and the
  current person; pick who **covers** instead and, optionally, a **Through**
  date to cover a whole span in one action (the "cover a teammate who is out for the week" flow). This writes a roster override for the chosen day(s). Levels
  that point directly at a user (not a roster) are shown but not reassignable
  here — edit the escalation chain instead.
- **Click a day background** (not a chip) to open the day detail: every
  chain's levels with the resolved person, shift window, and status, plus a
  **Replace** shortcut per roster-backed level and an inline maintenance
  action.
- **Drag across day backgrounds** (or Ctrl/Cmd-click days) to select multiple
  days, then **Maintenance window…** in the floating bar to suppress paging
  for them. Non-contiguous selections create one window per contiguous run of
  days. `Esc` (or a plain click) clears the selection.

Quick actions default to full days; fine-tune times in Rosters → overrides or
Maintenance Windows.

---

## 7. Maintenance Windows

Maintenance windows suppress known noisy periods.

When an incoming alert matches an active maintenance window scope, OpsMender drops it at intake. It does not create a visible incident and does not show a suppressed incident in the main incident list.

In v1, maintenance windows support selecting multiple services. Team scopes may be supported where the data model allows it; if not, service scopes are the safe default.

---

## 8. Notifications

Notifications is the Paging & On-call surface for personal and operator delivery.

Sections:

- My Routing: the current user's delivery channels and destinations.
- Quiet Hours: personal notification preferences where appropriate.
- Routing by Priority: map `P0`/`P1`/`P2`/`P3` to configured channels.
- Sessions / Chat: session behavior for chat-capable adapters only.

Supported channels depend on configured adapters, such as Slack, Microsoft Teams, Discord, Telegram, Email, SMS, WhatsApp, Signal, and custom adapters. Voice Call and SMS delivery use **Settings -> Voice & SMS calling**. The matching `OPSMENDER_TWILIO_*` environment variables still bootstrap fresh instances, but saved Settings values override them. Voice calls read the incident summary and accept keypad actions: `1` acknowledge, `2` escalate, and `*` repeat.

Stakeholder communication lives under Reports. Admins and operators can export
incident CSV/PDF reports, while admins can schedule recurring email delivery.

---

## 9. Setup Walkthrough

1. Create a team at `/dashboard/paging/teams`.
2. Create the team's escalation chain at `/dashboard/paging/escalation-chains`.
3. Create a service at `/dashboard/paging/services`; choose priority, MCP servers, and up to three Models.
4. Copy the service intake URL and configure your monitor to POST alerts to it.
5. Create one or more roster schedules at `/dashboard/paging/rosters`.
6. Add maintenance windows for planned work at `/dashboard/paging/maintenance-windows`.
7. Configure operator delivery, quiet hours, routing, and chat behavior at
   `/dashboard/paging/notifications`.
8. Fire a synthetic alert and confirm incident creation, operator routing, and
   notification flow. An AI session **auto-starts immediately** when the resolved
   autonomy tier is **T0**; for **T1/T2** the session starts after an
   Admin/Operator **acknowledges** the incident (or you can start one manually).

---

## 10. Surfaces

| Concept | UI route | API surface |
|---|---|---|
| Teams | `/dashboard/paging/teams` | `/teams` |
| Escalation Chains | `/dashboard/paging/escalation-chains` | `/escalation-chains`, `/services/{id}/escalation-chains` |
| Services and intake | `/dashboard/paging/services` | `/services`, `/api/v1/intake/{service_token}` |
| Rosters | `/dashboard/paging/rosters` | `/rosters` |
| On Call Schedule | `/dashboard/on-call-schedule` | `/paging/teams/{id}/on-call-calendar` |
| Maintenance Windows | `/dashboard/paging/maintenance-windows` | `/maintenance-windows` |
| Notifications | `/dashboard/paging/notifications` | `/users/me/notification-preferences`, `/webhook-triggers`, notification connector APIs |
| Incident ack / take / release / paging panel | `/dashboard/incidents/*` | `/incidents/{id}/ack`, `/take`, `/release`, `/paging` |

Legacy routes for old bookmarks can remain, but new v1 setup should use the routes above.
