# Integrations Guide

AIM is built to sit at the center of your incident response ecosystem. It ingests alerts from your existing monitoring tools and broadcasts updates to your collaboration platforms.

## 1. Incident Ingest Adapters

AIM provides a unified `/incidents/ingest` webhook endpoint. To secure and route incoming alerts, you generate **Ingest Tokens**.

AIM natively supports several popular monitoring tools:
- **LegacyAlertVendor:** Parses LegacyAlertVendor webhooks to extract incident details and severity.
- **Datadog:** Parses Datadog monitor alerts.
- **AWS CloudWatch (SNS):** Parses CloudWatch ALARM and OK states sent via SNS.
- **Azure Monitor:** Parses the Common Alert Schema v2.
- **GCP Cloud Monitoring:** Parses incident webhook v1.2.
- **Oracle Cloud (OCI):** Parses CHRONOS_NOTIFICATION alarms.
- **LegacyAlertRelay:** Parses LegacyAlertRelay alert webhooks.

**Universal (Auto) Adapter:**
If your tool is not listed above, AIM provides an `auto` provider option. The Universal Adapter uses an LLM to dynamically inspect the incoming JSON payload, learn its structure, and extract the title, description, and severity automatically. It caches the structural mapping for performance on subsequent alerts.

## 2. Chat Bot Connectors

AIM can expose selected incident workflows through external chat platforms. Connector setup lives in **Config** > **Integrations** and is also available through the `/bot-connectors` API.

Credentials are write-only: AIM shows whether credentials exist and which keys are stored, but it never returns raw credential values.

### Telegram

Telegram currently supports incident lookup, session status, and approval commands:

- `/incidents` lists the five most recent incidents.
- `/incident <incident-id>` shows one incident.
- `/sessions` lists the five most recent sessions.
- `/session <session-id>` shows one session.
- `/approvals` lists pending approval requests.
- `/approve <approval-id>` approves a pending request.
- `/reject <approval-id>` rejects a pending request.
- `/chat <session-id> <message>` relays a message into the target session's
  co-pilot chat (requires the `copilot_chat` capability). The assistant
  reply appears asynchronously in the AIM dashboard; outbound delivery of
  the reply back into Telegram is a planned follow-up.
- `/help` lists supported commands.

Required connector credentials:

```text
bot_token=<telegram-bot-token>
webhook_secret=<random-shared-secret>
```

Optional connector config:

```json
{
  "allowed_chat_ids": ["-1001234567890"],
  "rate_limit_per_minute": 30
}
```

`rate_limit_per_minute` defaults to 30 and applies per-chat. Set to `0`
to disable. Every inbound command is recorded in the `bot_action_audit`
table along with its outcome (`ok`, `bad_args`, `not_found`,
`capability_denied`, `chat_not_allowed`, `rate_limited`,
`unknown_command`, `delivery_failed`).

#### Outbound delivery

When the connector has the `notifications` capability and at least one
chat in `allowed_chat_ids`, AIM pushes a Telegram message for each
session lifecycle event (`session.created`, `session.awaiting_approval`,
`session.active`, `session.completed`, `session.failed`,
`session.timed_out`) to every listed chat.

When the connector has the `copilot_chat` capability, every co-pilot
assistant reply for a session is also relayed back to the Telegram
chat(s) that originated `/chat <session-id> ...` for that session — this
closes the round-trip so operators can converse with the co-pilot
entirely from Telegram. Outbound delivery uses the connector's
`bot_token` credential and records every send in `bot_action_audit`.

Set the Telegram webhook URL to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/telegram/webhook
```

When registering the webhook with Telegram, configure the secret token so Telegram sends `X-Telegram-Bot-Api-Secret-Token: <webhook_secret>` on each update.

Signal and WhatsApp are planned next and will use the same persisted connector model.

## 3. Outbound Webhooks

AIM can push real-time updates about incident sessions, AI actions, and SLA/SLO violations to external platforms.

1. Navigate to **Config** > **Webhooks**.
2. AIM supports formatted payloads for:
   - **Slack:** Sends beautifully formatted block-kit messages with incident details and links.
   - **Microsoft Teams:** Sends adaptive cards.
   - **Sumo Logic:** Sends structured JSON for ingestion into log analytics.
   - **Generic:** Sends a standard JSON payload containing the event data.

## 4. Docker Deployment Basics

If you are deploying AIM in a production environment, use the provided Dockerfiles.

- The repository includes a `docker-compose.yml` that orchestrates the backend (`fastapi`), frontend (`nextjs`), and the PostgreSQL database.
- **Environment Variables:** Ensure you map `DATABASE_URL` and your encryption keys (`AIM_SECRET_KEY`) securely.
- **Networking:** The MCP servers can be run as sidecar containers or standalone services, provided the AIM backend container has network access to them.
