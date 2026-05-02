This guide is intended for Incident Commanders and Operators using AIM to triage, investigate, and resolve live incidents.

> [!NOTE]
> All incidents, sessions, and audit logs are scoped to your active **Organization**. You will only see data belonging to the organization you are currently logged into.

## 1. The Triage Flow

When an incident is ingested (either manually or via an external alert), it appears on the **Incidents Dashboard**.

1. **Review:** Click on an incident to view its details, including title, description, severity, and any linked SLA targets.
2. **Start Session:** If an AI session hasn't auto-started, click **Start Session**. This provisions a dedicated AI agent context for the incident.
3. **Investigation:** Open the **Session Details** view to interact with the AI.

## 2. Interacting with Session Chat

The Session Chat is your primary interface with the AI agent.

- **Prompting:** You can give the AI high-level commands ("Investigate the database CPU spike") or specific instructions ("Query the `users` table for recent deadlocks").
- **Transparency:** The chat interface displays exactly which MCP tools the AI is calling, the parameters it passes, and the results returned by your infrastructure.
- **Guidance:** If the AI gets stuck or makes an incorrect assumption, simply correct it in the chat. The AI maintains full context of the conversation.

## 3. Approvals

AIM enforces safety through "Tiers". If an AI session is operating in a tier that requires human approval for state-mutating actions (e.g., executing a database write or restarting a pod), the AI will pause.

1. Navigate to the **Approvals** dashboard or look for the prompt in the Session Chat.
2. Review the exact command or tool the AI intends to run.
3. Click **Approve** to allow execution, or **Reject** to block it.
4. If rejected, you can provide a reason in the chat so the AI can adjust its approach.

## 4. The Audit Log

Every action taken by the AI is immutably recorded in the **Audit Log**.

- The Audit Log provides a chronological trace of all tool executions, approvals, and system state changes.
- It is invaluable for post-incident reviews (post-mortems) to understand exactly what the AI did, when, and who approved it.

## 5. Rollback Behavior

If the AI takes an action that worsens the incident, or if you need to revert a configuration change made during triage:

1. Locate the specific action in the Session Chat or Audit Log.
2. Depending on the MCP tool capabilities, you can ask the AI directly to "Rollback your last change."
3. AIM includes explicit rollback integrations for specific infrastructure changes (e.g., Kubernetes deployments, feature flags) if the corresponding MCP server supports it.
