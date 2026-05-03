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

#### Identity / RBAC

Read-only commands work for any chat user with access to the connector's
allowed chats. Mutating commands (`/approve`, `/reject`, `/chat`)
additionally require the Telegram user ID (`message.from.id`) to be
linked to an AIM user. Admins create the mapping via:

```bash
curl -X POST https://<your-aim-url>/bot-connectors/<connector-id>/user-links \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"platform_user_id": "12345678", "aim_user_id": "<aim-user-uuid>"}'
```

Linked users still need an AIM role of `admin` or `operator` to run any
mutating command. Viewers are blocked with a `role_denied` reply.
Unlinked users see a "not linked" reply that includes their Telegram
user ID for the admin to copy.

Admins can manage these mappings directly in the dashboard under
**Config** > **Integrations** by clicking the **"Links"** button on the
Telegram connector. Alternatively, use the API:

```bash
curl -X POST https://<your-aim-url>/bot-connectors/<connector-id>/user-links \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"platform_user_id": "12345678", "aim_user_id": "<aim-user-uuid>"}'
```

Set the Telegram webhook URL to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/telegram/webhook
```

When registering the webhook with Telegram, configure the secret token so Telegram sends `X-Telegram-Bot-Api-Secret-Token: <webhook_secret>` on each update.

### Signal

Signal uses the open-source `signal-cli-rest-api` bridge
(<https://github.com/bbernhard/signal-cli-rest-api>) as the gateway.
The same set of commands as Telegram is supported (`/incidents`,
`/incident`, `/sessions`, `/session`, `/approvals`, `/approve`,
`/reject`, `/chat`, `/help`).

Required connector credentials:

```text
service_url=http://<signal-cli-rest-api-host>:8080
bot_number=+15555550100         # the registered Signal service number
webhook_secret=<random-shared-secret>
```

Set the inbound webhook URL on your relay to:

```text
POST https://<your-aim-url>/bot-connectors/<connector-id>/signal/webhook
```

`signal-cli-rest-api` does not sign its own webhooks, so AIM expects an
intermediary (nginx, Caddy, or a small forwarder) to inject:

```text
X-AIM-Webhook-Secret: <webhook_secret>
```

Replies are delivered asynchronously through the bridge — Signal does
not support inline webhook responses. Every send is recorded in
`bot_action_audit` with `command = "notify:..."` or `"copilot_relay"`.

### WhatsApp (Meta Cloud API)

WhatsApp integration uses the official Meta Cloud API. The same set of
commands as Telegram is supported (`/incidents`, `/incident`,
`/sessions`, `/session`, `/approvals`, `/approve`, `/reject`,
`/chat`, `/help`).

Required connector credentials:

```text
access_token=<meta-system-user-token>
phone_number_id=<meta-phone-number-id>
verify_token=<random-shared-secret-for-meta-challenge>
app_secret=<meta-app-secret-for-hmac-verification>
```

Set the inbound webhook URL in the Meta App Dashboard to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/whatsapp/webhook
```

Ensure you select `messages` under **Webhook fields** in the Meta
configuration. AIM verifies every inbound request using HMAC-SHA256
signatures (`X-Hub-Signature-256`) against your `app_secret`.

Identity mapping (RBAC) works the same as Telegram: use the
**"Links"** button in the dashboard and use the user's phone number
(e.g., `15555550100`) as the platform user ID.

### Slack (Events API)

Slack integration uses the Slack Events API. Unlike outbound webhooks, the Slack Bot Connector supports interactive commands (`/incidents`, `/incident`, `/approvals`, etc.).

Required connector credentials:

```text
bot_token=xoxb-...          # Bot User OAuth Token
signing_secret=...          # App Credentials > Signing Secret
```

Set the **Request URL** in the Slack App Dashboard (under **Event Subscriptions**) to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/slack/webhook
```

Ensure you subscribe to the `message.channels` bot event. AIM verifies every inbound request using HMAC-SHA256 signatures (`X-Slack-Signature`) against your `signing_secret`.

Identity mapping (RBAC): Use the Slack User ID (e.g., `U0123456789`) as the platform user ID.

### Discord (Interactions)

Discord integration uses the Interactions API (webhooks) for low-latency command handling and the Bot API for outbound notifications.

Required connector credentials:

```text
bot_token=...               # Bot Token from Portal
public_key=...              # Application Public Key (hex string)
```

Set the **Interactions Endpoint URL** in the Discord Developer Portal to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/discord/webhook
```

AIM verifies every inbound interaction using Ed25519 signatures (`X-Signature-Ed25519`) against your `public_key`.

Identity mapping (RBAC): Use the Discord User ID (e.g., `123456789012345678`) as the platform user ID.

### Mattermost (Outgoing Webhooks)

Mattermost integration uses Outgoing Webhooks for inbound commands and the Mattermost API for outbound delivery.

Required connector credentials:

```text
bot_token=...               # Personal Access Token or Bot Token
service_url=https://...     # Your Mattermost instance URL
webhook_token=...           # The token generated by Mattermost for the outgoing webhook
```

Set the **Callback URLs** in the Mattermost Outgoing Webhook settings to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/mattermost/webhook
```

Ensure the **Content Type** is set to `application/x-www-form-urlencoded`.

Identity mapping (RBAC): Use the Mattermost User ID (e.g., `abc123xyz`) as the platform user ID.

### Matrix (Element)

Matrix integration uses the Client-Server API for outbound delivery and can receive inbound events via an App Service or custom webhook relay.

Required connector credentials:

```text
homeserver_url=https://...   # Your Matrix Homeserver URL
access_token=syt_...         # Bot account access token
webhook_secret=...           # Shared secret for inbound webhook verification
```

Set the inbound webhook URL on your Matrix relay/app-service to:

```text
POST https://<your-aim-url>/bot-connectors/<connector-id>/matrix/webhook
```

AIM expects an `Authorization: Bearer <webhook_secret>` header on inbound requests.

Identity mapping (RBAC): Use the Matrix User ID (e.g., `@user:matrix.org`) as the platform user ID.

### Feishu / Lark (Events)

Feishu integration uses the Events API v2. It supports automatic URL verification and tenant access token management.

Required connector credentials:

```text
app_id=cli_...              # App ID from Developer Console
app_secret=...              # App Secret
verification_token=...      # Event Subscriptions > Verification Token
```

Set the **Request URL** in the Feishu Developer Console (under **Event Subscriptions**) to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/feishu/webhook
```

Ensure you subscribe to the `im.message.receive_v1` event.

Identity mapping (RBAC): Use the Feishu Open ID (e.g., `ou_...`) as the platform user ID.

### DingTalk (Robot)

DingTalk integration uses Outgoing Robots for commands and the Robot Webhook API for notifications.

Required connector credentials:

```text
app_key=...                 # App Key (for API delivery)
app_secret=...              # App Secret (for signature verification)
```

Optional connector config:

```json
{
  "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=..."
}
```

If `webhook_url` is provided in the config, AIM will use it for all outbound delivery. Otherwise, it will use the App Key/Secret to fetch a token and use the standard Robot API.

Set the **POST URL** in the DingTalk Robot settings to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/dingtalk/webhook
```

Ensure you enable **Signature** verification in the robot settings using your `app_secret`.

Identity mapping (RBAC): Use the DingTalk Sender ID (e.g., `$...$`) as the platform user ID.

### WeCom (WeChat Work)

WeCom integration supports enterprise-grade secure messaging with AES-256-CBC encryption.

Required connector credentials:

```text
corpid=ww...                # Enterprise ID
corpsecret=...              # Agent Secret
agentid=...                 # Application Agent ID
token=...                   # Token for signature verification
encoding_aes_key=...        # AES Key for message decryption
```

Set the **URL** in the WeCom Management Console (under **Customer Service** or **Apps**) to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/wecom/webhook
```

AIM automatically handles the decryption and signature verification of the XML payloads.

Identity mapping (RBAC): Use the WeCom UserID (e.g., `Siddharth`) as the platform user ID.

### Weixin (WeChat Official Account)

Weixin integration supports standard Official Account message processing.

Required connector credentials:

```text
appid=wx...                 # Official Account AppID
appsecret=...               # AppSecret
token=...                   # Token for signature verification
```

Set the **URL** in the WeChat Official Platform settings to:

```text
https://<your-aim-url>/bot-connectors/<connector-id>/weixin/webhook
```

Identity mapping (RBAC): Use the Weixin OpenID (e.g., `o...`) as the platform user ID.

### SMS (Twilio)

SMS integration supports incident updates and simple command replies via Twilio.

Required connector credentials:

```text
account_sid=AC...           # Twilio Account SID
auth_token=...              # Twilio Auth Token
phone_number=+1...          # Your Twilio Phone Number
```

Required connector config:

```json
{
  "webhook_url": "https://<your-aim-url>/bot-connectors/<connector-id>/twilio/webhook"
}
```

Set the **A MESSAGE COMES IN** webhook in the Twilio Phone Number settings to the `webhook_url` above. AIM verifies every inbound SMS using Twilio's signature validation.

Identity mapping (RBAC): Use the sender's phone number (e.g., `+15550100`) as the platform user ID.

### Email (Mailgun)

Email integration supports incident reporting and replies via Mailgun.

Required connector credentials:

```text
mailgun_api_key=key-...     # Mailgun API Key
mailgun_domain=mg...        # Mailgun Domain
from_email=aim@yourdomain.com # Sender address
```

Set up a **Route** in Mailgun to forward emails to:

```text
POST https://<your-aim-url>/bot-connectors/<connector-id>/email/webhook
```

AIM verifies the Mailgun signature if the API key is provided.

Identity mapping (RBAC): Use the sender's email address (e.g., `operator@company.com`) as the platform user ID.

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
