# Troubleshooting Guide

This guide covers common issues you might encounter while deploying, configuring, or operating the Opsmender AI (Opsmender).

## 1. Login Issues

**Symptom:** Unable to log in with the default credentials, or getting a 401 Unauthorized error immediately after deployment.
**Resolution:**
- Verify that the backend database has been properly initialized. Opsmender uses Alembic for database migrations. Ensure the migration scripts have run successfully.
- If using an external SSO provider or reverse proxy (like an Identity Aware Proxy), ensure the proxy is correctly passing the authenticated user headers (e.g., `X-Forwarded-User`) to the Opsmender backend, and that Opsmender is configured to trust that proxy.

## 2. Provider Key Errors

**Symptom:** The AI session fails to start, or the chat immediately responds with an "Authentication Error" or "Invalid API Key."
**Resolution:**
- Navigate to **Config** > **Models**.
- Ensure the API key for your selected provider is entered correctly.
- Verify that your API key has the necessary permissions and sufficient billing credits.
- If using AWS or GCP, ensure the environment where the Opsmender backend is running has the correct IAM roles or Service Account JSON configured.

## 3. MCP Connectivity Issues

**Symptom:** The AI attempts to use a tool, but the chat shows an error like "Failed to connect to MCP server" or "Tool execution timed out."
**Resolution:**
- **For Stdio (Command-line) Servers:** Verify that the command specified in the Skill configuration is available in the Opsmender backend's system `PATH` (e.g., `python`, `npx`). Ensure any necessary dependencies are installed in that environment.
- **For SSE (Web) Servers:** Ensure the Opsmender backend container can resolve the hostname of the MCP server. Check for network policies or firewalls blocking traffic between the Opsmender backend and the SSE endpoint.
- **Logs:** Check the Opsmender backend application logs for specific traceback errors related to the MCP client subprocess.

## 4. Approval Stalls

**Symptom:** An AI session seems stuck "Thinking..." for a long period, but no approval prompt appears in the chat.
**Resolution:**
- The AI might be waiting for an approval that failed to render in the UI due to a WebSocket disconnection. Refresh the page.
- Navigate to the **Approvals** tab in the main dashboard. If there is a pending approval there, action it.
- Check the **Audit Log** to see if the tool execution was silently blocked by a Tier 0 timeout or a system-level safety constraint.

## 5. WebSocket / Live-Update Issues

**Symptom:** The Session Chat does not update automatically when the AI responds. You have to manually refresh the page to see new messages.
**Resolution:**
- Opsmender uses WebSockets for real-time chat updates. Ensure your reverse proxy (e.g., Nginx, Traefik) or Load Balancer is configured to support WebSocket upgrades.
- Check the browser's developer console for `WebSocket connection failed` errors.
- If running locally via Docker Compose, ensure port mapping is correct and no local firewall is blocking the WS protocol.
