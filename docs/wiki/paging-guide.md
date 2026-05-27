# Paging Guide

This is the top-level guide to OpsMender's paging surface — the system that decides who gets paged, when, on which channel, and what happens if they don't answer. It ties together the concepts that ship across Sprints 33–40 (services, teams, rosters, escalation chains, priority rules, response modes, maintenance windows, notification preferences, incident assignment) into a single operator-friendly story.

If you only want a one-line summary: **OpsMender owns paging end-to-end inside the product. You configure rosters, chains, and priority rules inside OpsMender, and OpsMender fans incidents out to your operators on Slack DM, Teams DM, Email, or SMS.**

For the deep-dive data-model spec, see [`docs/paging-model.md`](../paging-model.md). For platform-specific chat-surface details, see [Slack as your paging surface](slack-paging-surface.md), [Teams as your paging surface](teams-paging-surface.md), or [Notification Preferences](notification-preferences.md). This page is the conceptual orientation that ties them together.

---

## 1. The incident-response loop

Every paged incident walks the same five-stage loop:

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │                                                                          │
  │   1. Alert fires                                                         │
  │      Prometheus / Datadog / CloudWatch / Azure Monitor / etc. POSTs      │
  │      to /incidents/ingest with a service-scoped token.                   │
  │                                                                          │
  │   2. Priority rule decides response mode                                 │
  │      First-match-wins on the alert payload. Result: P0/P1/P2/P3 +        │
  │      response mode (auto_resolve / notify / page / escalate_immediate).  │
  │                                                                          │
  │   3. Escalation chain runs                                               │
  │      page mode → fire step 0 (notify the on-call user via channel        │
  │      preferences). Hard 15-minute inactivity timeout. Once paged,        │
  │      stay paged (additive — chain never un-pages anyone).                │
  │                                                                          │
  │   4. Operator acknowledges                                               │
  │      Click Acknowledge in Slack DM / Teams DM / web UI, or run           │
  │      /ack in chat. Chain pauses. Assignment created.                     │
  │                                                                          │
  │   5. Resolution                                                          │
  │      The AI session runs in parallel and may auto-resolve the            │
  │      incident. Or the operator takes over, fixes the root cause,         │
  │      and clicks Resolve. Either way the chain cancels and the            │
  │      incident.status flips to resolved.                                  │
  │                                                                          │
  └──────────────────────────────────────────────────────────────────────────┘
```

The end-to-end loop is covered by `tests/test_e2e_paging_flow.py::TestIncidentResponseLoop` — that test is the canonical regression guard for the full surface.

---

## 2. The conceptual model

OpsMender's paging hierarchy maps to the way SRE teams already organize themselves:

```
Organization (tenant boundary)
  └── Teams (org-chart inside one tenant)
       └── Services (the thing being monitored)
            ├── Rosters (who is on duty)
            ├── Escalation Chains (if no ack, who is next)
            ├── Priority Rules (which P0/P1/P2/P3 to assign)
            └── Maintenance Windows (suppress paging in time range)

User × Org
  └── Notification Preferences (channels, routing per priority, quiet hours)
```

The AI workflow stays in front of this model — every incident enters the AI loop first; paging fires only when the priority rule resolves to a paging Response Mode.

### Response Mode (the dial that controls human involvement)

| Mode | What happens | Typical use |
|---|---|---|
| `auto_resolve` | AI handles end-to-end. No human paged. No channel post. Audit log only. | Tier-2 read-only checks; known transient noise; AI-detected false-positives. |
| `notify` | AI handles it. Channel post for visibility. No on-call ping. | Routine ops, low-stakes self-healing, P2/P3 noise that should be visible but not interruptive. |
| `page` | AI starts working; on-call user is paged in parallel through the escalation chain. Default for P0 / P1. | Standard incident response. |
| `escalate_immediate` | Skip the chain — page everyone on the team simultaneously. AI still runs but humans are involved from second zero. | Catastrophic outages, security breaches. |

Mode is decided **at incident-creation time** from `(priority, service, override-rules)` and is locked thereafter. The AI cannot mutate Response Mode during a session. The AI *can* resolve an incident (e.g., if it determines the alert was a false positive), which closes the page lifecycle naturally.

Default priority → mode mapping (overridable per priority rule):

| Priority | Default mode |
|---|---|
| P0 | `page` |
| P1 | `page` |
| P2 | `notify` |
| P3 | `notify` |

### Rosters (deterministic on-call rotation)

A roster is the ordered list of users who take turns being on call for a service. OpsMender ships three rotation patterns out of the box:

- **`weekly`** — each member is on call for 7 days, then hands off to the next.
- **`daily`** — each member is on call for 24 hours, then hands off.
- **`custom_n_days`** — any positive integer; defaults to 7.

`on_call_at(roster_snapshot, t)` deterministically resolves who is on call at any timestamp. The math is pure — the same `(roster, t)` always returns the same user — and runs in the roster's configured IANA time zone (so a weekly roster anchored on Mondays handoffs at 09:00 local, not 09:00 UTC). One-off overrides (someone is sick, swap shifts, on-call for the week of a launch) win over the schedule when active.

Performance: `on_call_at` runs at ~2 µs per call on a 50-member roster — see `tests/test_on_call_perf.py` for the benchmark, which guards 1k rosters × 50 members under a 500 ms total budget.

### Escalation chains (what happens if no one acks)

A chain is an ordered list of steps. Each step targets a `roster`, a `team`, or a specific `user`, and has a timeout in seconds. When step 0's timeout expires without an ack, step 1 fires (in addition to step 0 — once paged, always paged). When the chain runs out of steps or hits the hard 15-minute inactivity timeout, the chain enters `exhausted` and the audit log captures who never responded.

`escalate_immediate` mode fires **every step at the same time** — useful for the rare catastrophic incident where you don't want the chain spacing things out.

### Priority rules (first-match-wins)

A priority rule is a `(condition, priority, response_mode)` triple. Conditions are JSON predicates against the alert payload (case-insensitive; list values mean OR):

```json
{ "severity": ["critical", "p1"], "service": "checkout-api" }
```

The first active rule whose condition matches wins. If no rule matches, the incident falls back to P3 + `notify`. Optional one-way LLM escalation (`priority_llm_escalation_enabled`) can promote — but never demote — the priority based on the LLM's read of the incident; the override is audit-logged.

### Maintenance windows (suppress paging in a time range)

Scope-aware windows: `global`, `service`, `roster`, or `team`. When an incident lands inside an active window that covers its scope, the incident is **suppressed entirely** — no page, no chain, no notification — and `incidents.suppressed_by_maintenance_window_id` is stamped so the suppression is auditable. `escalate_immediate` is never downgraded, even by a maintenance window.

### Notification preferences (channels, routing, quiet hours)

Per-user, per-org. Each user picks which channels they want enabled (Slack DM, Teams DM, Email, SMS), supplies a destination per channel, and configures a priority-routing matrix (e.g. P0 → Slack + SMS; P1 → Slack; P2 → Email; P3 → none). Quiet hours support a start/end window in the user's IANA time zone plus a `min_priority_to_break` threshold (or `Never break`).

Per-org dedup: `organizations.notification_dedup_window_minutes` (default 10) prevents the same `(incident, user, channel)` from being notified twice within the window. The dispatcher records every attempt as a row in `incident_pages` with `delivery_status` (`sent` / `failed` / `skipped`) and a reason.

### Incident assignment (incident-scoped operator authority)

When a user acks an incident — via the web UI, a Slack button, or a slash command — an `incident_assignments` row is created with `assigned_to = user.id`. That assignee is granted operator privileges *for that specific incident*, regardless of their global role. So a viewer-role user who acked their team's incident can still release it or take action on it; they just can't act on other incidents.

Force-takeover by an admin (`POST /incidents/{id}/take {force: true}`) swaps the assignee and is audit-logged via `assigned_by = "admin_force"`.

---

## 3. Setup walkthrough

Concrete order to wire your first paging service:

1. **Pick a team and create it** under `/dashboard/paging/teams`. A team is the org-chart slice that owns one or more services.
2. **Create the service** under `/dashboard/paging/services`. Bind it to the team you just created. A service is the "thing being monitored" — `checkout-api`, `payment-gateway`, `aws-rds-prod`. One service per alert source is the usual pattern.
3. **Build the roster** under `/dashboard/paging/rosters`. Add members in shift order. Pick a pattern (`weekly` is the default), an anchor date (when the first member's first shift starts), and a handoff time (e.g. `09:00` local). Bind the roster to the service.
4. **Build the escalation chain** under `/dashboard/paging/escalation-chains`. Add step 0 with a target of `roster` and your roster id, timeout 300 seconds. Add step 1 with a higher-up target (the team lead, or a backup roster). Bind the chain to the service via the Services page's "Escalation chain" picker.
5. **Add a priority rule** under `/dashboard/paging/priority-rules`. The simplest rule is `{ "severity": "critical" }` → P1 → `page`. First-match-wins, so order rules from most-specific to most-generic.
6. **Each operator sets their notification preferences** under `/dashboard/paging/my-notifications`. Enable the channel they want paged on, paste the destination (Slack user id, Teams chat id, email, phone), and configure the priority-routing matrix and quiet hours.
7. **Wire the alert source.** Create an ingest token bound to the service at `/dashboard/ingest-tokens`, copy the raw token, and point your alerting system (Prometheus Alertmanager, Datadog, CloudWatch, etc.) at `https://<your-opsmender>/incidents/ingest` with the token in the `X-OpsMender-Token` header.
8. **Verify.** POST a synthetic alert with `severity: critical`. The priority rule should fire, the chain should kick off, and the on-call operator should see a Slack/Teams DM (or Email/SMS) within seconds. The end-to-end test `tests/test_e2e_paging_flow.py::TestIncidentResponseLoop` walks the same flow programmatically — useful as a reference for what to assert against.

### One-off maintenance window

When a planned change is about to happen and you want to silence pages for a service:

1. Go to `/dashboard/paging/maintenance-windows`.
2. Click "New maintenance window". Pick scope `service`, the service id, start/end timestamps, and an optional description.
3. Any incident that lands inside the window for that service is suppressed automatically. The incident detail page shows a "Paging suppressed by maintenance window" banner so the audit trail is intact.

---

## 4. Where each surface lives

| Concept | UI tab | API surface | Wiki page |
|---|---|---|---|
| Teams / Services / Rosters / Priority Rules | `/dashboard/paging/{teams,services,rosters,priority-rules}` | `/teams`, `/services`, `/rosters`, `/priority-rules` | [paging-model.md](../paging-model.md) |
| Escalation Chains | `/dashboard/paging/escalation-chains` | `/escalation-chains`, `/services/{id}/escalation-chains` | [paging-model.md](../paging-model.md) |
| Maintenance Windows + Notification Preferences | `/dashboard/paging/{maintenance-windows,my-notifications}` | `/maintenance-windows`, `/users/me/notification-preferences`, `/organizations/{id}/notification-settings` | [Notification Preferences](notification-preferences.md) |
| Slack page card + slash commands + channel mirror | (chat) | `/bot/slack/interactions`, `/bot/slack/commands` | [Slack as your paging surface](slack-paging-surface.md) |
| Teams adaptive card + bot activity | (chat) | `/bot/teams/activity` | [Teams as your paging surface](teams-paging-surface.md) |
| Mobile-friendly response | `/dashboard/incidents/detail`, `/dashboard/sessions/detail`, `/dashboard/paging/*` | n/a | [Responding from your phone](mobile-incident-response.md) |
| Incident ack / take / release / chain panel | `/dashboard/incidents/detail` | `/incidents/{id}/ack`, `/take`, `/release`, `/chain`, `/paging` | this page |

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Alert lands but no page fires | Priority rule doesn't match → falls back to P3+notify. | Check the rule condition against the actual payload shape. Adjust ordering (first match wins). |
| Page fires but no Slack DM | `OPSMENDER_SLACK_BOT_TOKEN` unset, or the user's notification prefs don't enable Slack. | Set the env var; confirm the user has Slack DM enabled with a valid `slack_user_id` destination. |
| Operator clicks Acknowledge in Slack but nothing happens | `bot_user_links` row missing for that Slack user id. | Have the user link their Slack identity to their OpsMender account. The endpoint returns a friendly "your account isn't linked" ephemeral. |
| Chain advances even though someone acked | Possible — `ack` pauses the chain. Look at `incident_pages` to confirm the ack row landed before the next step's timer fired. | Check the ack audit row's `recorded_at` against the next step's `next_step_due_at`. |
| Notification suppressed unexpectedly | Active maintenance window covering the incident's scope. | Check `incidents.suppressed_by_maintenance_window_id` and the matching `maintenance_windows` row. |
| Paged user never received the notification | Dedup window. The same `(incident, user, channel)` was already notified within `notification_dedup_window_minutes`. | Check `incident_pages` for an earlier `(sent)` row for the same triple. Tune the org-level dedup window if needed. |

For platform-specific failures (Slack signing-secret mismatches, Teams Bot Framework JWT issues, Graph permission gaps), see the dedicated [Slack](slack-paging-surface.md) and [Teams](teams-paging-surface.md) troubleshooting tables.

---

## 6. End-to-end verification recipe

When you want to prove the loop works on a fresh deployment, without booting real Slack or Twilio:

```bash
# 1. Run the canonical end-to-end test (drives the full HTTP surface).
.venv/bin/python -m pytest tests/test_e2e_paging_flow.py -v

# 2. Run the on-call resolver perf benchmark.
.venv/bin/python -m pytest tests/test_on_call_perf.py -v
```

Both ship as regression guards on `main`. A failure in either is a signal that the paging surface has regressed in a way that operators will feel.
