# Integrations Guide

OpsMender is built to sit at the center of your incident response ecosystem. It
ingests alerts, delivers incident updates, and connects the AI response loop to
external systems through encrypted, tier-governed integration connectors.

## 1. External system connectors

Admins manage source-control, ticketing, documentation, observability, and
infrastructure connectors at **Admin → Integrations**.

- Credentials are entered as provider-specific JSON and encrypted as one opaque
  Fernet payload. The API never returns values; it only reports whether auth is
  configured and which keys exist.
- `base_url` supports self-hosted editions where relevant.
- **Test** runs the adapter's cheap connection probe and records healthy/error
  status, timestamp, and the last error.
- Disabled connectors are not exposed to incident sessions.
- Enabled capabilities become internal tools governed by the same Skill Gate,
  safety tier, approval queue, and audit trail as MCP tools. Mutating actions
  require Tier 1 approval by default.

The **Custom HTTP** kind is the reference adapter in the foundation.

### GitHub

- Hosted base URL: leave blank (`https://api.github.com`).
- Enterprise Server: enter the API base or instance root; roots normalize to
  `/api/v3`.
- PAT credentials: `{"token":"..."}`.
- App credentials:
  `{"app_id":"...","installation_id":"...","private_key":"-----BEGIN PRIVATE KEY-----..."}`.
- Common config: `{"owner":"acme","repo":"service","api_version":"2022-11-28"}`.

Capabilities include repository/file reads, issue list/create/comment,
pull-request create/merge, and commit/pull-request links to incidents.

### GitLab

- Hosted base URL: leave blank (`https://gitlab.com/api/v4`).
- Self-managed: enter the API base or instance root; roots normalize to
  `/api/v4`.
- PAT credentials: `{"token":"..."}`.
- OAuth credentials: `{"access_token":"..."}`.
- Common config: `{"project":"group/project"}`.

Capabilities include project/file reads, issue list/create/comment,
merge-request create/merge, and commit/merge-request links to incidents.

PR/MR merge capabilities always require explicit Operator Approval regardless
of the workspace's autonomous tier.

### Bitbucket

- Cloud: leave the base URL blank; use
  `{"email":"admin@example.com","api_token":"..."}` and
  `{"workspace":"acme","repo":"service"}`.
- Data Center: enter the instance URL and use
  `{"edition":"data_center","project":"OPS","repo":"service"}`.

Capabilities cover repository/file reads and pull-request create/merge. The
Cloud edition also supports issue list/create. Merge always requires approval.

### Azure DevOps

- Services: leave the base URL blank and configure
  `{"organization":"acme","project":"Operations","repository":"service"}`.
- Server: enter the collection base URL.
- PAT credentials: `{"token":"..."}`; OAuth:
  `{"access_token":"..."}`.

Capabilities cover Repos repository/file and pull-request workflows plus
Boards work-item read/create/update. Pull-request completion always requires
approval.

### Jira and Confluence

Enter the Cloud site or on-premises instance URL. Cloud API-token credentials
use `{"email":"admin@example.com","api_token":"..."}`; OAuth uses
`{"access_token":"..."}`. Set `{"edition":"on_prem"}` for an on-premises
edition.

- Jira config commonly includes `{"project_key":"OPS","issue_type":"Task"}`.
  It can read/create/comment on issues and list/apply transitions.
- Confluence config uses `{"space_id":"..."}`. It can read runbooks and
  create/update postmortem pages.

### ServiceNow

Enter the instance URL and configure a table such as
`{"table":"incident"}`. Basic credentials are
`{"username":"...","password":"..."}`; OAuth uses an access token.
Capabilities read, create, and update Table API records. Wave 1 does not
perform continuous state synchronization.

### Linear

Use `{"api_key":"..."}` and optionally configure
`{"team_id":"..."}`. OAuth access tokens are also supported. Capabilities
read/list/create/update issues through Linear's GraphQL API.

### Notion

Use `{"api_key":"..."}` for an integration token or an OAuth access token.
Configure a default parent with `{"parent_page_id":"..."}`. Capabilities read
page markdown, create pages, and append document content using Notion API
version `2026-03-11`.

## 2. Incident Ingest Adapters

OpsMender provides inbound alert intake for monitoring tools. In v1, the legacy `/incidents/ingest` token backend remains available, but the product concept is moving toward **Service Webhooks**: each service exposes a unique alert URL with an embedded unguessable secret so external monitors can POST directly without separate API-key headers.

OpsMender natively supports several popular monitoring tools:
- **Datadog:** Parses Datadog monitor alerts.
- **AWS CloudWatch (SNS):** Parses CloudWatch ALARM and OK states sent via SNS.
- **Azure Monitor:** Parses the Common Alert Schema v2.
- **GCP Cloud Monitoring:** Parses incident webhook v1.2.
- **Oracle Cloud (OCI):** Parses CHRONOS_NOTIFICATION alarms.

**Universal (Auto) Adapter:**
If your tool is not listed above, OpsMender provides an `auto` provider option. The Universal Adapter uses an LLM to dynamically inspect the incoming JSON payload, learn its structure, and extract the title, description, and severity automatically. It caches the structural mapping for performance on subsequent alerts.

## 3. Notification Channels

OpsMender can expose selected incident workflows through external chat platforms. Channel setup lives under **Paging & On-call** > **Notification Channels** and remains available through the existing `/bot-connectors` API.

Credentials are write-only: OpsMender shows whether credentials exist and which keys are stored, but it never returns raw credential values.

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
  reply appears asynchronously in the OpsMender dashboard; outbound delivery of
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
chat in `allowed_chat_ids`, OpsMender pushes a Telegram message for each
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
linked to an OpsMender user. Admins create the mapping via:

```bash
curl -X POST https://<your-opsmender-url>/bot-connectors/<connector-id>/user-links \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"platform_user_id": "12345678", "opsmender_user_id": "<opsmender-user-uuid>"}'
```

Linked users still need an OpsMender role of `admin` or `operator` to run any
mutating command. Viewers are blocked with a `role_denied` reply.
Unlinked users see a "not linked" reply that includes their Telegram
user ID for the admin to copy.

Admins can manage these mappings directly in the dashboard under
**Paging & On-call** > **Notification Channels** by clicking the **"Links"** button on the
Telegram connector. Alternatively, use the API:

```bash
curl -X POST https://<your-opsmender-url>/bot-connectors/<connector-id>/user-links \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"platform_user_id": "12345678", "opsmender_user_id": "<opsmender-user-uuid>"}'
```

Set the Telegram webhook URL to:

```text
https://<your-opsmender-url>/bot-connectors/<connector-id>/telegram/webhook
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
POST https://<your-opsmender-url>/bot-connectors/<connector-id>/signal/webhook
```

`signal-cli-rest-api` does not sign its own webhooks, so OpsMender expects an
intermediary (nginx, Caddy, or a small forwarder) to inject:

```text
X-OpsMender-Webhook-Secret: <webhook_secret>
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
https://<your-opsmender-url>/bot-connectors/<connector-id>/whatsapp/webhook
```

Ensure you select `messages` under **Webhook fields** in the Meta
configuration. OpsMender verifies every inbound request using HMAC-SHA256
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
https://<your-opsmender-url>/bot-connectors/<connector-id>/slack/webhook
```

Ensure you subscribe to the `message.channels` bot event. OpsMender verifies every inbound request using HMAC-SHA256 signatures (`X-Slack-Signature`) against your `signing_secret`.

Identity mapping (RBAC): Use the Slack User ID (e.g., `U0123456789`) as the platform user ID.

### Microsoft Teams (Graph + Bot Framework)

Teams Notification Channels use Microsoft Graph app-only credentials for
outbound Adaptive Cards and Microsoft Bot Framework JWTs for native actions.

Configure:

```text
tenant_id=...               # Azure AD directory ID
client_id=...               # Graph application ID
client_secret=...           # Graph application secret value
bot_app_id=...              # Azure Bot registration app ID
default_chat_id=...         # Teams chat destination
```

Set the Azure Bot **Messaging endpoint** to:

```text
POST https://<your-opsmender-url>/bot/teams/activity
```

Enable verified Teams actions on the Notification Channel only after the Bot
Framework app ID is set. OpsMender validates the Microsoft-signed JWT audience,
issuer, and time claims before processing an action.

Identity mapping (RBAC): Use the user's Azure AD object ID
(`activity.from.aadObjectId`) as the platform user ID.

### Discord (Interactions)

Discord integration uses the Interactions API (webhooks) for low-latency command handling and the Bot API for outbound notifications.

Required connector credentials:

```text
bot_token=...               # Bot Token from Portal
public_key=...              # Application Public Key (hex string)
```

Set the **Interactions Endpoint URL** in the Discord Developer Portal to:

```text
https://<your-opsmender-url>/bot-connectors/<connector-id>/discord/webhook
```

OpsMender verifies every inbound interaction using Ed25519 signatures (`X-Signature-Ed25519`) against your `public_key`.

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
https://<your-opsmender-url>/bot-connectors/<connector-id>/mattermost/webhook
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
POST https://<your-opsmender-url>/bot-connectors/<connector-id>/matrix/webhook
```

OpsMender expects an `Authorization: Bearer <webhook_secret>` header on inbound requests.

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
https://<your-opsmender-url>/bot-connectors/<connector-id>/feishu/webhook
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

If `webhook_url` is provided in the config, OpsMender will use it for all outbound delivery. Otherwise, it will use the App Key/Secret to fetch a token and use the standard Robot API.

Set the **POST URL** in the DingTalk Robot settings to:

```text
https://<your-opsmender-url>/bot-connectors/<connector-id>/dingtalk/webhook
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
https://<your-opsmender-url>/bot-connectors/<connector-id>/wecom/webhook
```

OpsMender automatically handles the decryption and signature verification of the XML payloads.

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
https://<your-opsmender-url>/bot-connectors/<connector-id>/weixin/webhook
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
  "webhook_url": "https://<your-opsmender-url>/bot-connectors/<connector-id>/twilio/webhook"
}
```

Set the **A MESSAGE COMES IN** webhook in the Twilio Phone Number settings to the `webhook_url` above. OpsMender verifies every inbound SMS using Twilio's signature validation.

Identity mapping (RBAC): Use the sender's phone number (e.g., `+15550100`) as the platform user ID.

### Mailgun Email

The current email Notification Channel supports incident reporting, replies,
and outbound delivery through Mailgun. It is not a generic SMTP or IMAP
connector.

Required connector credentials:

```text
mailgun_api_key=key-...     # Mailgun API Key
mailgun_domain=mg...        # Mailgun Domain
from_email=opsmender@yourdomain.com # Sender address
```

Set up a **Route** in Mailgun to forward emails to:

```text
POST https://<your-opsmender-url>/bot-connectors/<connector-id>/email/webhook
```

OpsMender requires the Mailgun API key and rejects inbound requests that do not
include a valid Mailgun signature.

Identity mapping (RBAC): Use the sender's email address (e.g., `operator@company.com`) as the platform user ID.

### SMTP Email

Use **SMTP Email** for outbound incident notifications through a hosted SMTP
provider or an infrastructure relay. Configure the SMTP host/port, STARTTLS or
implicit TLS (or no transport upgrade for a trusted internal relay), optional
username/password, sender address, and default recipient.

SMTP Email is outbound-only: it does not support IMAP, inbound replies, user
identity mapping, or native incident actions. Connector-level SMTP settings are
separate from `OPSMENDER_SMTP_*`, which remains the account invite and
password-reset mail configuration.

### Home Assistant

Home Assistant integration allows OpsMender to push actionable notifications to your HASS dashboard and receive commands from HASS automations.

Required connector credentials:

```text
service_url=https://...     # Your HASS instance URL
access_token=...            # Long-Lived Access Token from HASS profile
webhook_secret=...          # Optional secret for inbound verification
```

Set the webhook URL in your HASS automation or REST command to:

```text
POST https://<your-opsmender-url>/bot-connectors/<connector-id>/homeassistant/webhook
```

Inbound payloads should include `action` or `message`.

Identity mapping (RBAC): Use the HASS User ID or the entity ID triggering the webhook as the platform user ID.

### BlueBubbles (iMessage)

BlueBubbles integration allows iMessage-based incident management.

Required connector credentials:

```text
server_url=https://...     # Your BlueBubbles Server URL
password=...               # BlueBubbles API Password
```

Set the Webhook URL in the BlueBubbles Server settings to:

```text
https://<your-opsmender-url>/bot-connectors/<connector-id>/bluebubbles/webhook
```

Identity mapping (RBAC): Use the sender's phone number or Apple ID (e.g., `+15550123`) as the platform user ID.

## 3. Reports and email delivery

Viewer-facing delivery is report-based. Configure organization SMTP under
**Config**, then use **Reports** for CSV/PDF exports and scheduled stakeholder
email. Real-time incident status belongs to Track-lane Slack, Teams, or
EventBridge Notification Channels.

## 4. Docker Deployment Basics

If you are deploying OpsMender in a production environment, use the provided Dockerfiles.

- The repository includes a `docker-compose.yml` that orchestrates the backend (`fastapi`), frontend (`nextjs`), and the PostgreSQL database.
- **Environment Variables:** Ensure you map `DATABASE_URL` and your encryption keys (`OPSMENDER_SECRET_KEY`) securely.
- **Networking:** The MCP servers can be run as sidecar containers or standalone services, provided the OpsMender backend container has network access to them.
