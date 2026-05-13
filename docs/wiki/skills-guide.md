# Skills Guide

In Opsmender, "Skills" are the capabilities provided to the AI agent. Opsmender uses the **Model Context Protocol (MCP)** to define and execute these skills. By attaching an MCP server to Opsmender, you grant the AI the ability to interact with your real-world infrastructure.

## 1. What is an MCP Server?
An MCP Server is an external process or service that exposes a standard set of "tools" to the AI. Instead of giving the AI direct arbitrary execution power, you define specific tools (e.g., `get_pod_logs`, `query_user_db`) that the AI can call. 

## 2. Managing Skills

You can manage your skills from the **Skills** tab in the Opsmender Dashboard.

- **Import:** Add a pre-existing MCP server configuration.
- **Edit:** Modify the command, environment variables, or transport mechanism of a skill.
- **Clone:** Duplicate an existing skill to quickly create a variation (e.g., duplicating a dev-environment skill and changing the credentials for production).

## 3. Attaching to MCP Servers
Opsmender supports two transport mechanisms for MCP servers:

1. **Stdio (Command-line):** The MCP server runs as a local subprocess. Opsmender communicates with it over standard input/output.
   - *Example:* `python mcp_server.py` or `npx @modelcontextprotocol/server-postgres`
2. **SSE (Server-Sent Events):** The MCP server runs as an independent web service, and Opsmender connects to it over HTTP.
   - *Example:* `https://my-internal-mcp-server.local/sse`

## 4. Enforcement & Safety Tiers

Opsmender enforces strict safety guarantees using **Tiers**.
- **Tier 0:** Sandbox mode. The AI is completely restricted and any command execution requires explicit human approval. Additionally, hard timeouts and system-level enforcement mechanisms block any uncontrolled escalation.
- **Tier 1:** Safe read-only mode. The AI can execute non-mutating commands (e.g., querying logs) without approval, but anything else requires approval.
- **Tier 2+:** Fully autonomous mode (use with extreme caution).

When attaching an MCP server to Opsmender, you map the server to a specific Tier limit. If an AI agent attempts to use a tool that exceeds its current Tier permissions, Opsmender pauses execution and requests **human approval** in the dashboard.

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
