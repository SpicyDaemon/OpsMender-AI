# Skills Guide

In OpsMender, "Skills" are the capabilities provided to the AI agent. OpsMender uses the **Model Context Protocol (MCP)** to define and execute these skills. By attaching an MCP server to OpsMender, you grant the AI the ability to interact with your real-world infrastructure.

## 1. What is an MCP Server?
An MCP Server is an external process or service that exposes a standard set of "tools" to the AI. Instead of giving the AI direct arbitrary execution power, you define specific tools (e.g., `get_pod_logs`, `query_user_db`) that the AI can call. 

## 2. Managing Skills

You can manage your skills from the **Skills** tab in the OpsMender Dashboard.

- **Find:** Search by skill name, description, focus area, or MCP server. Use the MCP server chips to filter to one server or the global fallback skill.
- **Import:** Add a pre-existing MCP server configuration.
- **Edit:** Modify the command, environment variables, or transport mechanism of a skill.
- **Clone:** Duplicate an existing skill to quickly create a variation (e.g., duplicating a dev-environment skill and changing the credentials for production).

## 3. Attaching to MCP Servers
OpsMender supports two transport mechanisms for MCP servers:

1. **Stdio (Command-line):** The MCP server runs as a local subprocess. OpsMender communicates with it over standard input/output.
   - *Example:* `python mcp_server.py` or `npx @modelcontextprotocol/server-postgres`
2. **HTTP / SSE:** The MCP server runs as an independent web service, and OpsMender connects to it over HTTP.
   - *Example:* `https://my-internal-mcp-server.local/sse`

For URL-based MCP servers, admins can use **Connect** from Config -> MCP Servers to run the OAuth 2.1 + PKCE flow. OpsMender discovers the server's authorization metadata, validates the callback issuer, and stores access/refresh tokens encrypted in the database. Stdio servers still get credentials from environment variables.

## 4. Enforcement & Safety Tiers

OpsMender enforces strict safety guarantees using **Tiers**.
- **Tier 0:** Sandbox mode. The AI is completely restricted and any command execution requires explicit human approval. Additionally, hard timeouts and system-level enforcement mechanisms block any uncontrolled escalation.
- **Tier 1:** Safe read-only mode. The AI can execute non-mutating commands (e.g., querying logs) without approval, but anything else requires approval.
- **Tier 2+:** Fully autonomous mode (use with extreme caution).

When attaching an MCP server to OpsMender, you map the server to a specific Tier limit. If an AI agent attempts to use a tool that exceeds its current Tier permissions, OpsMender pauses execution and requests **human approval** in the dashboard.

## 5. Examples

Here are some examples of Skills you can attach:

### Example 1: Database Query (Read-Only)
You can use the official Postgres MCP server to allow the AI to investigate database deadlocks or user issues.
- **Type:** `stdio`
- **Command:** `npx`
- **Args:** `-y @modelcontextprotocol/server-postgres postgresql://localhost/mydb`

### Example 2: File System Access
Give the AI the ability to read configuration files or application logs.
- **Type:** `stdio`
- **Command:** `npx`
- **Args:** `-y @modelcontextprotocol/server-filesystem /var/log/my-app`

### Example 3: Kubernetes Troubleshooting
You can build a custom Python MCP server that wraps `kubectl` commands like `get pods` or `logs`.
- **Type:** `stdio`
- **Command:** `python`
- **Args:** `/path/to/k8s_mcp_server.py`
