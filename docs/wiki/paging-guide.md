# Paging Guide

This is the v1 guide to OpsMender's paging and on-call surface. The product model is intentionally small:

- Services receive alerts and own incident priority.
- Teams own services and escalation chains.
- Rosters define who is on call for coverage windows.
- Maintenance windows drop matching alerts during planned work.
- Notifications covers operator delivery, quiet hours, routing by priority, and
  chat sessions. Stakeholder updates are delivered through incident reports.

If you only want a one-line summary: create a Team, create its Escalation Chain, add a Service with an intake endpoint, attach a Roster schedule, then configure Notifications.

For the data-model + algorithm overview (former D-021 — Paging Model), see [`docs/PROMPT_CONTEXT.md`](../PROMPT_CONTEXT.md) (Architecture → Paging model) and `backend/paging/on_call.py`. For platform-specific chat details, see [Slack as your paging surface](slack-paging-surface.md) and [Teams as your paging surface](teams-paging-surface.md). For the broader notification model and version-scope decisions, see [`docs/PROMPT_CONTEXT.md`](../PROMPT_CONTEXT.md) (Scope & Roadmap Guardrail).

---

## 1. The Incident-Response Loop

Every paged incident walks the same simple loop:

1. Alert fires and POSTs to a service endpoint: `POST /api/v1/intake/{service_token}`.
2. The service sets the priority: `P0 Critical`, `P1 High`, `P2 Medium`, or `P3 Low`.
3. If an active maintenance window matches the service or team scope, the alert is dropped and no visible incident is created.
4. OpsMender creates the incident and starts the AI session.
5. The team's escalation chain pages the roster or user levels for that service.
6. An operator acknowledges, works the incident, and resolves it with or without AI assistance.

The AI does not assign or override priority in v1. It may use the service's ordered Preferred MCP servers to reduce tool-selection noise, but preferred MCPs are recommendations, not a hard allowlist.

---

## 2. Conceptual Model

```
Organization
  └── Teams
       ├── Escalation Chains
       └── Services
            ├── Generated intake URL
            ├── Fixed priority
            ├── Preferred MCP servers
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
- Ordered Preferred MCP servers.
- Up to three ranked Preferred Models.

Preferred MCP servers tell the AI which configured MCP servers to try first for incidents from that service. Operators can still manually ask the AI to use another configured MCP server during a session.

Preferred Models are tried in order. Disabled or unavailable models are skipped,
then OpsMender falls back to another enabled model. The model selected when the
incident is created becomes the default for its AI session when possible;
operators can still switch models during the session.

Manual incidents created from the Incidents page must be linked to an active
service so ownership, routing, escalation, MCP, and model preferences are
unambiguous.

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

Interactions (admin):

- **Click a person (chip)** to replace who's on call for that level. The
  "Replace who's on call" dialog shows the chain, level, roster, and the
  current person; pick who **covers** instead and, optionally, a **Through**
  date to cover a whole span in one action (the "a teammate is out this week, John
  covers" flow). This writes a roster override for the chosen day(s). Levels
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

Supported channels depend on configured adapters, such as Slack, Microsoft Teams, Discord, Telegram, Email, SMS, WhatsApp, Signal, and custom adapters.

Stakeholder communication lives under Reports. Admins and operators can export
incident CSV/PDF reports, while admins can schedule recurring email delivery.

---

## 9. Setup Walkthrough

1. Create a team at `/dashboard/paging/teams`.
2. Create the team's escalation chain at `/dashboard/paging/escalation-chains`.
3. Create a service at `/dashboard/paging/services`; choose priority, Preferred MCP servers, and up to three Preferred Models.
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
