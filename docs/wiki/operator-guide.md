This guide is intended for Incident Commanders and Operators using OpsMender to triage, investigate, and resolve live incidents.

> [!NOTE]
> All incidents, sessions, and audit logs are scoped to your active **Organization**. You will only see data belonging to the organization you are currently logged into.

## 1. The Triage Flow

When an incident is ingested (either manually or via an external alert), it appears on the **Incidents Dashboard**.

1. **Triage the list:** Use the table search, status/severity/source chips, Last activity date range, sorting, and column controls to narrow the incident set.
2. **Bulk-handle obvious rows:** Select one or more rows to bulk **Acknowledge** or **Resolve** without opening each incident.
3. **Review:** Click on an incident to open the command surface. The detail page now keeps the main response controls in a sticky command strip, a right-side context rail for service/team/owner/escalation state, and a single timeline that interleaves AI actions with paging events and inbound alert evidence.
4. **Start Session:** If an AI session hasn't auto-started, click **Start Session** from the command strip or the timeline header. This provisions a dedicated AI agent context for the incident.
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

## 3. Approvals

OpsMender enforces safety through "Tiers". If an AI session is operating in a tier that requires human approval for state-mutating actions (e.g., executing a database write or restarting a pod), the AI will pause.

1. Navigate to the **Approvals** dashboard or look for the prompt in the Session Chat.
2. Review the exact command or tool the AI intends to run.
3. Click **Approve** to allow execution, or **Reject** to block it.
4. If rejected, you can provide a reason in the chat so the AI can adjust its approach.

## 4. The Audit Log

Every action taken by the AI is recorded in the **Audit Log** for the lifetime
of its session. Permanently deleting the owning incident also deletes that
session and its tool audit history.

- The Audit Log provides a chronological trace of all tool executions, approvals, and system state changes.
- The Activity page lets you search, sort, filter by type/tier/status, narrow by timestamp range, hide/show columns, and expand rows to inspect the exact Parameters and Result JSON for a tool call.
- It is invaluable for post-incident reviews (post-mortems) to understand exactly what the AI did, when, and who approved it. The Audit Log feeds the **Timeline** section of the dedicated postmortem editor — see [postmortem-guide.md](postmortem-guide.md).

## 5. Writing Postmortems

Once an incident reaches `resolved` or `closed`, the Incident Command Strip surfaces a **Create postmortem** action that opens a dedicated editor at `/dashboard/incidents/postmortem?id=<incident_id>`. The editor ships with an Edit/Preview toggle, the seven recommended sections (Summary · Impact · Timeline · Root cause · Resolution · Lessons learned · Memory candidates), Save / Clear / Reset-to-template actions, and a one-line tip for each section. Memory candidates are intended as the shortlist you'll later promote into `/dashboard/memories`. Admins and operators can edit; viewers see the editor read-only. The full walkthrough lives in [postmortem-guide.md](postmortem-guide.md).

## 6. Keyboard quick access

Press **Cmd+K** (Mac) / **Ctrl+K** (everywhere else) to open the **Command Palette** from anywhere in the dashboard. The palette has two categories:

- **Navigate** — every sidebar route (Dashboard, Incidents, Approvals, Paging surfaces, AI Agent surfaces, Admin, etc.) with fuzzy keyword matching (e.g. type "audit log" to land on Activity, "schedule" for Rosters).
- **Actions** — New incident · Fire test incident · Open pending approvals · Show who's on-call · Run environment scan.

Keyboard model: `↑` / `↓` move the highlight, `Enter` executes, `Esc` closes. The shortcut is reserved — it works even when an input is focused, which is the point. The existing `?` overlay (keyboard shortcut help) lists `Cmd K` for discovery.

## 7. Rollback Behavior

If the AI takes an action that worsens the incident, or if you need to revert a configuration change made during triage:

1. Locate the specific action in the Session Chat or Audit Log.
2. Depending on the MCP tool capabilities, you can ask the AI directly to "Rollback your last change."
3. OpsMender includes explicit rollback integrations for specific infrastructure changes (e.g., Kubernetes deployments, feature flags) if the corresponding MCP server supports it.
