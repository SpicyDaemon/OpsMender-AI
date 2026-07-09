This guide covers the core configuration and integration points for administrators managing the OpsMender AI (OpsMender) platform.

For authentication see two dedicated guides:

- **[Auth Guide](auth-guide.md)** — default email + admin-invite flow used by ~95% of self-hosted installs.
- **[Advanced Auth Guide](advanced-auth-guide.md)** — optional OIDC, SAML, and custom-domain login behavior for the single workspace.

For day-to-day user-lifecycle operations (invites, password resets, deactivation, soft delete, bootstrap admins, auth-method badges) see the [People Guide](people-guide.md).

## 1. Authentication overview

OpsMender ships **simple by default, enterprise-ready underneath**.

- **Default mode** (`OPSMENDER_ADVANCED_AUTH_ENABLED=false`): one workspace, email + admin invites, three roles (`admin` / `operator` / `viewer`), no org switcher in the TopBar. Full details in [Auth Guide](auth-guide.md).
- **Advanced auth** (`OPSMENDER_ADVANCED_AUTH_ENABLED=true`, or an already-configured provider): OIDC / SAML forms surface in **Settings -> Workspace**; custom domains can show the right login buttons before authentication. Full details in [Advanced Auth Guide](advanced-auth-guide.md).

Existing configured SSO/SAML providers keep working regardless of the flag — settings never silently disappear when the flag flips off (D-027 "settings never silently disappear" rule).

## 2. Runtime Configuration

You can manage runtime configurations via the **Config** tab in the dashboard.
These settings apply globally to the OpsMender instance.

Key configurations include:
- **Default Tier:** The workspace default shown in the sidebar. Each session
  still resolves its own tier from service, skill, and request precedence, so a
  Tier 1 approval can appear while the workspace default remains Tier 2.
- **Auto-Start Policies:** Conditions under which OpsMender will automatically start an AI session upon incident ingestion.
- **SLA Poller Defaults:** The non-AI Reliability checker that repeatedly probes HTTP/TCP targets. HTTP targets can treat exact codes, status classes (`2xx`), ranges (`200-299`), or expected error codes such as `404` as healthy.

### Monolith and distributed deployment

Monolith remains the default:

```dotenv
OPSMENDER_DEPLOYMENT_MODE=monolith
OPSMENDER_ENVIRONMENT=production
```

For separate API, worker, scheduler, and dispatcher processes, use
`docker/docker-compose.distributed.yml`. It assigns
`OPSMENDER_SERVICE_ROLE=api|worker|scheduler|dispatcher` per container. The API
is exposed on port 8000 and external webhook/callback ingress on dispatcher
port 8001. Both share Postgres and Redis. Existing installations using
`OPSMENDER_DEPLOYMENT_MODE=development|production` continue to run as
monoliths.

## 3. Model Configuration

OpsMender supports multiple LLM providers. Navigate to **Models** in the sidebar (`/dashboard/models`) to configure them.

Supported providers as of Sprint 62:

| Provider | Notes |
|----------|-------|
| **Anthropic** | Claude models. Set `ANTHROPIC_API_KEY` in `.env`. |
| **OpenAI** | GPT models. Set `OPENAI_API_KEY` in `.env`. |
| **Azure OpenAI** | Azure-hosted OpenAI deployments. Requires `base_url` (your resource endpoint) and `api_version`. |
| **AWS Bedrock** | Uses the native AWS credential chain. Requires an AWS Region; optional AWS Profile name if you want a specific shared-config profile. No raw API key is stored in OpsMender. |
| **GCP Vertex AI** | Uses ADC. Requires a GCP Project and GCP Location. Supports `google/...`, `anthropic/...`, and `meta/...` publisher/model IDs so partner models stay explicit. |
| **Ollama** | Local runtime. Default `base_url` is `http://localhost:11434`. No API key required. |
| **OpenAI-compatible** | Any OpenAI-API-shape endpoint: vLLM, LM Studio, OpenRouter, Together, Groq, Fireworks, Anyscale, and most local OpenAI-shape runtimes. **Requires** `base_url`; API key is optional (some local endpoints accept any string or none). |

Per Sprint 62 design, OpsMender stores only the **environment variable name** for each provider's secret, never the raw value. Set the actual key in `.env` and reference it from the dashboard. Cloud providers use native credential discovery instead of long-lived pasted secrets: AWS Bedrock uses the AWS credential chain and GCP Vertex AI uses ADC.

To add a model config:

1. Click **New model config**.
2. Select your provider — only the fields that provider needs are shown (e.g. OpenAI-compatible shows Base URL; Azure OpenAI shows both Base URL and API Version; Bedrock shows AWS Region + optional AWS Profile; Vertex AI shows GCP Project + GCP Location).
3. For Bedrock, enter the AWS Region first, then click **Refresh Catalog** if you want the live Bedrock model list for that region/profile.
4. For Vertex AI, enter the GCP Project + Location first, then click **Refresh Catalog** if you want the live `google/...`, `anthropic/...`, and `meta/...` model suggestions for that project/location.
5. Pick a model from the discovered catalog, or click **Type manual model ID** if discovery is unavailable or the model isn't reported (e.g. a proxy that doesn't implement `/v1/models`).
6. Optionally set **Concurrent Sessions**. `0` means unlimited. When every
   response model is full, incident sessions enter the durable priority queue;
   ingest/triage classification does not consume these slots.

Capacity is enforced per saved model config. The cap is local to OpsMender, so it
does not protect a provider account's combined global limit if the same API key
is used elsewhere. Queue wait, approval hold,
extension warning, and scheduler sweep intervals are configured with
`OPSMENDER_SESSION_QUEUE_TTL_SECONDS`,
`OPSMENDER_APPROVAL_HOLD_TTL_SECONDS`,
`OPSMENDER_APPROVAL_EXTENSION_WARNING_SECONDS`, and
`OPSMENDER_SESSION_QUEUE_SWEEP_SECONDS`.
6. Set **Concurrent Sessions** if the provider/model needs a per-workspace
   incident-response limit. `0` means unlimited. When a model is full,
   OpsMender tries the Service's next model, then the workspace
   default, then another active model. This cap does not apply to ingest/triage
   classification.
7. Save. Model discovery is cached for 60 seconds for local/proxy endpoints and 1 hour for cloud catalogs so the page stays snappy.

## 4. MCP Servers and Skills

OpsMender uses the Model Context Protocol (MCP) to interact with your infrastructure. MCP servers and Skills are managed separately: MCP servers define the connection, while Skills define the allowed operations for that connection.

1. Go to **Config** > **MCP** to add or test an MCP server.
2. Provide the command or transport details for the MCP server (stdio, SSE, or HTTP).
3. Go to **Skills** to import, edit, clone, or bind `SKILL.md` content to an MCP server.
4. Use tiers and Skill classifications together to control what OpsMender can execute.

## 5. External integrations

Open **Integrations** under Admin to configure external system connectors.
Choose a kind, optional self-hosted base URL, authentication type, credential
JSON, and adapter configuration JSON. Credentials are encrypted and
write-only. Use **Test** to record connection health; disable a connector to
remove its capabilities from incident sessions without deleting it.

Integration actions reuse the normal Safety Tier, Skill Gate, Operator
Approval, and Tool Activity audit path. State-changing actions are not a bypass
around MCP governance.

Native connector coverage includes GitHub, GitLab, Bitbucket, Azure DevOps,
Jira, Confluence, ServiceNow, Linear, Notion, and Kubernetes.
Provider-specific setup examples appear directly below the base URL,
credentials, and configuration fields. Repository merges and Kubernetes
remediation are routed through Operator Approval.

## 6. Notification Channels

Workspace-level notification channels are managed under **Paging & On-call** > **Notification Channels**.

1. Click **Add Channel**.
2. Choose the platform (e.g., `telegram`, `slack`, `discord`, `teams`, `mattermost`, `matrix`, `whatsapp`, `signal`, `lark`, `dingtalk`, `wecom`, `twilio`, `email`).
3. Add platform-specific connector settings as JSON.
4. Add credentials as `key=value` lines (e.g., `bot_token=...`).
5. Select allowed capabilities (e.g., `incident_lookup`, `session_status`, `approvals`, `notifications`).
6. Click **Check configuration** to validate the saved configuration (enabled,
   credentials, capabilities, destination, team scope, and native-action
   readiness are each graded pass/warn/fail), or **Send live test** to probe the
   provider connection and post a real test message to the channel's
   destination.

Telegram webhook URL:

```text
https://<your-opsmender-url>/bot-connectors/<connector-id>/telegram/webhook
```

Configure Telegram to send the `X-Telegram-Bot-Api-Secret-Token` header with the same value as the connector's `webhook_secret`.

Supported Telegram commands:
- `/incidents`
- `/incident <incident-id>`
- `/sessions`
- `/session <session-id>`
- `/approvals`
- `/approve <approval-id>`
- `/reject <approval-id>`
- `/help`

## 7. Email and incident reports

Configure **Config → Email / SMTP** first. The same organization SMTP server
powers invitations, password resets, test messages, and scheduled reports.
Then open **Reports** to download CSV/PDF incident metrics or create weekly,
monthly, and quarterly recipient schedules.

## 8. Audit retention and archive

Audit entries are retained in Postgres for 90 days by default. A dedicated job
runs daily at 02:00 UTC. With archival disabled, expired rows are pruned
directly. With archival enabled, OpsMender first writes gzip-compressed JSONL
objects to S3-compatible storage and deletes only rows whose upload succeeds.

Configure the global fallback in `.env`:

```dotenv
AUDIT_RETENTION_DAYS=90
AUDIT_ARCHIVE_ENABLED=true
AUDIT_ARCHIVE_S3_BUCKET=opsmender-audit
AUDIT_ARCHIVE_S3_PREFIX=audit/
# Optional for non-AWS S3-compatible storage:
AUDIT_ARCHIVE_S3_ENDPOINT_URL=https://objects.example.com
# Optional when the normal AWS credential chain is not used:
AUDIT_ARCHIVE_AWS_ACCESS_KEY_ID=...
AUDIT_ARCHIVE_AWS_ACCESS_KEY_SECRET=...
```

Objects are named `{prefix}{YYYY-MM-DD}.jsonl.gz` using each audit entry's UTC
date. Per-workspace `audit_entries` settings under **Config → Storage &
retention** override `AUDIT_RETENTION_DAYS`; disabling that category prevents
both archival and pruning for the workspace.

## 9. Alert Intake

To ingest incidents automatically from external tools (e.g., Datadog, CloudWatch, or generic webhook senders), create a service and use the Services area as the home for Alert Intake / Service Webhook setup.

1. Go to **Paging & On-call** > **Services**.
2. Create or open the service that owns the alerts.
3. Use the service-specific alert intake URL when available. The v1 security model is an embedded unguessable secret in the URL, so external monitors can POST directly without managing separate API-key headers.
4. The legacy `/dashboard/ingest-tokens` route remains available for existing installs while service-level webhook UX matures.
