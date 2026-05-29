# Paging Guide

This is the v1 guide to OpsMender's paging and on-call surface. The product model is intentionally small:

- Services receive alerts and own incident priority.
- Teams own services and escalation chains.
- Rosters define who is on call for coverage windows.
- Maintenance windows drop matching alerts during planned work.
- Notifications covers operator delivery, viewer updates, quiet hours, routing by priority, and chat sessions.

If you only want a one-line summary: create a Team, create its Escalation Chain, add a Service with an intake endpoint, attach a Roster schedule, then configure Notifications.

For the deep-dive data-model spec, see [`docs/paging-model.md`](../paging-model.md). For platform-specific chat details, see [Slack as your paging surface](slack-paging-surface.md) and [Teams as your paging surface](teams-paging-surface.md).

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
       ├── Operator Delivery
       ├── Viewer Updates
       ├── Quiet Hours
       ├── Routing by Priority
       └── Sessions / Chat
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

Preferred MCP servers tell the AI which configured MCP servers to try first for incidents from that service. Operators can still manually ask the AI to use another configured MCP server during a session.

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

---

## 6. Maintenance Windows

Maintenance windows suppress known noisy periods.

When an incoming alert matches an active maintenance window scope, OpsMender drops it at intake. It does not create a visible incident and does not show a suppressed incident in the main incident list.

In v1, maintenance windows support selecting multiple services. Team scopes may be supported where the data model allows it; if not, service scopes are the safe default.

---

## 7. Notifications

Notifications is the single Paging & On-call page for delivery and update setup.

Sections:

- Operator Delivery: channels used to page admins and operators.
- Viewer Updates: read-only/status updates to viewer audiences or external workflows.
- Quiet Hours: personal notification preferences where appropriate.
- Routing by Priority: map `P0`/`P1`/`P2`/`P3` to configured channels.
- Sessions / Chat: session behavior for chat-capable adapters only.

Supported channels depend on configured adapters, such as Slack, Microsoft Teams, Discord, Telegram, Email, SMS, WhatsApp, Signal, and custom adapters.

---

## 8. Setup Walkthrough

1. Create a team at `/dashboard/paging/teams`.
2. Create the team's escalation chain at `/dashboard/paging/escalation-chains`.
3. Create a service at `/dashboard/paging/services`; choose priority and Preferred MCP servers.
4. Copy the service intake URL and configure your monitor to POST alerts to it.
5. Create one or more roster schedules at `/dashboard/paging/rosters`.
6. Add maintenance windows for planned work at `/dashboard/paging/maintenance-windows`.
7. Configure operator delivery, viewer updates, quiet hours, routing, and chat behavior at `/dashboard/paging/notifications`.
8. Fire a synthetic alert and confirm the incident, AI session, operator routing, and notification flow.

---

## 9. Surfaces

| Concept | UI route | API surface |
|---|---|---|
| Teams | `/dashboard/paging/teams` | `/teams` |
| Escalation Chains | `/dashboard/paging/escalation-chains` | `/escalation-chains`, `/services/{id}/escalation-chains` |
| Services and intake | `/dashboard/paging/services` | `/services`, `/api/v1/intake/{service_token}` |
| Rosters | `/dashboard/paging/rosters` | `/rosters` |
| Maintenance Windows | `/dashboard/paging/maintenance-windows` | `/maintenance-windows` |
| Notifications | `/dashboard/paging/notifications` | `/users/me/notification-preferences`, `/webhook-triggers`, notification connector APIs |
| Incident ack / take / release / paging panel | `/dashboard/incidents/*` | `/incidents/{id}/ack`, `/take`, `/release`, `/paging` |

Legacy routes for old bookmarks can remain, but new v1 setup should use the routes above.
