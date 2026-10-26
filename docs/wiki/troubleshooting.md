# Troubleshooting Guide

This guide covers common issues you might encounter while deploying, configuring, or operating the OpsMender AI (OpsMender).

## 1. Login Issues

**Symptom:** Unable to log in with the default credentials, or getting a 401 Unauthorized error immediately after deployment.
**Resolution:**
- Verify that the backend database has been properly initialized. OpsMender uses Alembic for database migrations. Ensure the migration scripts have run successfully.
- If using an external SSO provider or reverse proxy (like an Identity Aware Proxy), ensure the proxy is correctly passing the authenticated user headers (e.g., `X-Forwarded-User`) to the OpsMender backend, and that OpsMender is configured to trust that proxy.

## 2. Provider Key Errors

**Symptom:** The AI session fails to start, or the chat immediately responds with an "Authentication Error" or "Invalid API Key."
**Resolution:**
- Navigate to **Config** > **Models**.
- Ensure the API key for your selected provider is entered correctly.
- Verify that your API key has the necessary permissions and sufficient billing credits.
- If using AWS or GCP, ensure the environment where the OpsMender backend is running has the correct IAM roles or Service Account JSON configured.

## 3. MCP Connectivity Issues

**Symptom:** The AI attempts to use a tool, but the chat shows an error like "Failed to connect to MCP server" or "Tool execution timed out."
**Resolution:**
- **For Stdio (Command-line) Servers:** Verify that the command specified in the Skill configuration is available in the OpsMender backend's system `PATH` (e.g., `python`, `npx`). Ensure any necessary dependencies are installed in that environment.
- **For SSE (Web) Servers:** Ensure the OpsMender backend container can resolve the hostname of the MCP server. Check for network policies or firewalls blocking traffic between the OpsMender backend and the SSE endpoint.
- **Logs:** Check the OpsMender backend application logs for specific traceback errors related to the MCP client subprocess.

## 4. Approval Stalls

**Symptom:** An AI session seems stuck "Thinking..." for a long period, but no approval prompt appears in the chat.
**Resolution:**
- The AI might be waiting for an approval that failed to render in the UI due to a WebSocket disconnection. Refresh the page.
- Navigate to the **Approvals** tab in the main dashboard. If there is a pending approval there, action it.
- Check the **Audit Log** to see if the tool execution was silently blocked by a Tier 0 timeout or a system-level safety constraint.

## 5. WebSocket / Live-Update Issues

**Symptom:** The Session Chat does not update automatically when the AI responds. You have to manually refresh the page to see new messages.
**Resolution:**
- OpsMender uses WebSockets for real-time chat updates. Ensure your reverse proxy (e.g., Nginx, Traefik) or Load Balancer is configured to support WebSocket upgrades.
- Check the browser's developer console for `WebSocket connection failed` errors.
- If running locally via Docker Compose, ensure port mapping is correct and no local firewall is blocking the WS protocol.

## 6. A Session Ran No Tool Although the Model Proposed Some

**Symptom:** The session's Co-pilot panel says "The model's plan could not be read, so no action was taken." and the Event Stream shows no tool call.
**Resolution:**
- The plan step asks the model for a JSON array of actions, each with a `tool_name`. OpsMender reads the plan only when the answer is the plan: the array, or one such action on its own, either bare, after a sentence with nothing after it, or inside one Markdown code fence with no other bracket, brace or `tool_name` around it. Any other answer runs nothing, so a draft, a quoted action, a list of options or a cut-off answer never runs, and the summary says the plan could not be read.
- Small local models drift from the format more often. Try a larger model. Or open **Models**, click **Edit** on the model config the session uses and raise **Max Tokens**. An answer cut off at that limit holds no complete plan.
