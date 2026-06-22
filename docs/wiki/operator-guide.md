This guide is intended for Incident Commanders and Operators using OpsMender to triage, investigate, and resolve live incidents.

> [!NOTE]
> All incidents, sessions, and audit logs are scoped to your active **Organization**. You will only see data belonging to the organization you are currently logged into.

## 1. The Triage Flow

When an incident is ingested (either manually or via an external alert), it appears on the **Incidents Dashboard**.

1. **Triage the list:** Use the table search, status/severity/source chips, Last activity date range, sorting, and column controls to narrow the incident set.
2. **Bulk-handle obvious rows:** Select one or more rows to bulk **Acknowledge** or **Resolve** without opening each incident.
3. **Review:** Click on an incident to open the command surface. The detail page now keeps the main response controls in a sticky command strip, a right-side context rail for service/team/owner/escalation state, and a single timeline that interleaves AI actions with paging events and inbound alert evidence.
4. **Start Session:** If an AI session hasn't auto-started, click **Start Session** from the command strip or the timeline header. This provisions a dedicated AI agent context for the incident. If every configured response model is full, the session waits in the durable capacity queue; the incident list and session page show that state.
5. **Investigation:** Use the timeline rows to jump straight into the session sidecar or open the full **Session Details** view when you need the richer chat surface.

### Permanently deleting an incident

Admins see a compact trash icon on every incident row and in the incident
command strip. It is available for every status, including open incidents.
After confirmation, OpsMender cancels any tracked AI workflow and permanently
removes the incident, its sessions, comments, paging state, approvals, and tool
audit history. Independent ingest, bot-action, finding, and memory records are
retained with their incident/session references cleared. This action cannot be
undone. Operators and viewers never see or receive access to this action.

## 2. Interacting with Session Chat

The Session Chat is your primary interface with the AI agent.

- **Prompting:** You can give the AI high-level commands ("Investigate the database CPU spike") or specific instructions ("Query the `users` table for recent deadlocks").
- **Transparency:** The chat interface displays exactly which MCP tools the AI is calling, the parameters it passes, and the results returned by your infrastructure.
- **Guidance:** If the AI gets stuck or makes an incorrect assumption, simply correct it in the chat. The AI maintains full context of the conversation.

## 3. Starting a session by tier

The AI Autonomy Tier governs both what the AI may do and **how a session starts**:

- **Tier 0 — Autonomous:** a session auto-starts the moment the incident is created.
- **Tier 1 / Tier 2:** **no** session auto-starts. **Acknowledge** the incident first (this records you as the owner), then click **Start Session**. Starting a Tier 1/2 session is blocked until the incident is acknowledged.

Queued sessions are ordered P0→P3 and FIFO within the same priority. Model
selection is re-evaluated when the session reaches the front, so it can use a
better model that became available while it waited. Human paging is independent
and continues normally. You can cancel a queued session from its session page.
The Start Session modal also offers **Force start** as an explicit soft
override; confirm the warning before using it. The override is audited and the
session still counts toward model occupancy.

Acknowledgment means the incident is already human-owned, so OpsMender cancels
any session that was queued before the acknowledgment. Because Tier 1/2 starts
are acknowledgment-gated, if capacity is full at that point, wait for a slot or
use the explicit audited **Force start** override rather than leaving delayed
work behind.

## 4. Approvals (Tier 1 is interactive)

At **Tier 1**, the AI pauses on **every** state-mutating action it proposes (not just destructive ones); read-only investigation runs freely, and deny-listed actions are never offered. (For MCP Skills that declare an explicit per-operation Tier 1 policy, that policy decides.)

1. Find the prompt in the **Session Chat** (or the **Approvals** dashboard).
2. Review the exact tool + parameters the AI intends to run.
3. Choose one:
   - **Approve** — the action executes.
   - **Reject** — the action is blocked.
   - **Redirect** — type free-text guidance (e.g. "drain the node first, then restart") and the AI **re-plans** with your steering in context.
   - **Extend session** — resets the approval-hold timer when you need more time. OpsMender warns approvers shortly before the hold expires; expiry rejects the pending action, ends the session, and releases its model slot.

## 5. Intercept a running session (Stop / Override)

You can take control of any running session from the session page:

- **Stop** — immediately halts the AI (use this to take over manually elsewhere; an action already in flight may still finish on the target system).
- **Override** — stops the AI's current autonomy and **continues the same session** under your control at a less-autonomous tier (**Tier 1** approval-driven or **Tier 2** advisory). Overriding assigns the incident to you.

## 6. The Audit Log

Every action taken by the AI is recorded in the **Audit Log** for the lifetime
of its session. Permanently deleting the owning incident also deletes that
session and its tool audit history.

- The Audit Log provides a chronological trace of all tool executions, approvals, and system state changes.
- The Activity page lets you search, sort, filter by type/tier/status, narrow by timestamp range, hide/show columns, and expand rows to inspect the exact Parameters and Result JSON for a tool call.
- It is invaluable for post-incident reviews (post-mortems) to understand exactly what the AI did, when, and who approved it. The Audit Log feeds the **Timeline** section of the dedicated postmortem editor — see [postmortem-guide.md](postmortem-guide.md).

## 7. Writing Postmortems

Once an incident reaches its final `resolved` status, the Incident Command Strip surfaces a **Create postmortem** action that opens a dedicated editor at `/dashboard/incidents/postmortem?id=<incident_id>`. The editor ships with an Edit/Preview toggle, the seven recommended sections (Summary · Impact · Timeline · Root cause · Resolution · Lessons learned · Memory candidates), Save / Clear / Reset-to-template actions, and a one-line tip for each section. Memory candidates are intended as the shortlist you'll later promote into `/dashboard/memories`. Admins and operators can edit; viewers see the editor read-only. The full walkthrough lives in [postmortem-guide.md](postmortem-guide.md).

## 8. Keyboard quick access

Press **Cmd+K** (Mac) / **Ctrl+K** (everywhere else) to open the **Command Palette** from anywhere in the dashboard. The palette has two categories:

- **Navigate** — every sidebar route (Dashboard, Incidents, Approvals, Paging surfaces, AI Agent surfaces, Admin, etc.) with fuzzy keyword matching (e.g. type "audit log" to land on Activity, "schedule" for Rosters).
- **Actions** — New incident · Fire test incident · Open pending approvals · Show who's on-call · Run environment scan.

Keyboard model: `↑` / `↓` move the highlight, `Enter` executes, `Esc` closes. The shortcut is reserved — it works even when an input is focused, which is the point. The existing `?` overlay (keyboard shortcut help) lists `Cmd K` for discovery.

## 9. Rollback Behavior

If the AI takes an action that worsens the incident, or if you need to revert a configuration change made during triage:

1. Locate the specific action in the Session Chat or Audit Log.
2. Depending on the MCP tool capabilities, you can ask the AI directly to "Rollback your last change."
3. OpsMender includes explicit rollback integrations for specific infrastructure changes (e.g., Kubernetes deployments, feature flags) if the corresponding MCP server supports it.
