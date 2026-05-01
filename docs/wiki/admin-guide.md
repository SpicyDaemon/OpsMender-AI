# Administrator Guide

This guide covers the core configuration and integration points for administrators managing the AI Incident Manager (AIM) platform.

## 1. Authentication

AIM currently relies on local database authentication or a reverse proxy. 
By default, the initial setup comes with a local `admin` user.
If you deploy AIM in production, it is recommended to secure the frontend behind an Identity Aware Proxy (IAP) or SSO provider (e.g., Okta, Google Workspace).

## 2. Runtime Configuration

You can manage runtime configurations via the **Config** tab in the dashboard.
These settings apply globally to the AIM instance.

Key configurations include:
- **Default Tier:** The default safety tier for new sessions (e.g., Tier 2).
- **Auto-Start Policies:** Conditions under which AIM will automatically start an AI session upon incident ingestion.
- **SLA Poller Defaults:** The default interval for checking SLA target uptime.

## 3. Model Configuration

AIM supports multiple LLM providers. Navigate to **Config** > **Models** to configure them.

1. Select your preferred provider (e.g., OpenAI, Anthropic, GCP Vertex).
2. Input the necessary API Keys or Service Account JSONs.
3. Choose the specific default models for different tasks (e.g., GPT-4o for complex reasoning, Claude 3.5 Sonnet for rapid triage).

## 4. MCP Servers and Skills

AIM uses the Model Context Protocol (MCP) to interact with your infrastructure. MCP servers and Skills are managed separately: MCP servers define the connection, while Skills define the allowed operations for that connection.

1. Go to **Config** > **MCP** to add or test an MCP server.
2. Provide the command or transport details for the MCP server (stdio, SSE, or HTTP).
3. Go to **Skills** to import, edit, clone, or bind `SKILL.md` content to an MCP server.
4. Use tiers and Skill classifications together to control what AIM can execute.

## 5. Chat Bot Connectors

External chat bot connectors are managed in **Config** > **Integrations**.

1. Click **Add Connector**.
2. Choose the platform (`telegram`, `signal`, `whatsapp`, or `custom`).
3. Add non-secret connector settings as JSON. For Telegram, use `allowed_chat_ids` to restrict which chats can issue commands.
4. Add credentials as `key=value` lines. Telegram currently uses `bot_token` for readiness checks and `webhook_secret` for inbound webhook verification.
5. Select allowed capabilities. Telegram currently supports read-only `incident_lookup` commands.
6. Click **Test** to validate the saved configuration.

Telegram webhook URL:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/telegram/webhook
```

Configure Telegram to send the `X-Telegram-Bot-Api-Secret-Token` header with the same value as the connector's `webhook_secret`.

Supported Telegram commands:
- `/incidents`
- `/incident <incident-id>`
- `/help`

## 6. Webhooks & Triggers

You can set up outbound webhooks to notify external systems (like Slack, Microsoft Teams, or Sumo Logic) when specific events occur.

1. Go to **Config** > **Webhooks**.
2. Click **New Trigger**.
3. Select the event types to listen for:
   - `session.created`
   - `session.active`
   - `session.awaiting_approval`
   - `slo.burn_rate_violated`
4. Provide the target URL and optional authentication headers.

## 7. Ingest Tokens

To ingest incidents automatically from external tools (e.g., LegacyAlertVendor, Datadog), you must generate an Ingest Token.

1. Go to **Config** > **Ingest**.
2. Click **Generate Token**.
3. Select the provider (e.g., `legacy_alert_vendor`, `datadog`, or `auto` for universal LLM-based parsing).
4. Copy the generated token securely. It will not be shown again.
5. Configure your external tool to send a webhook POST request to `https://<your-aim-url>/incidents/ingest` with the header `X-AIM-Token: <your-token>`.
