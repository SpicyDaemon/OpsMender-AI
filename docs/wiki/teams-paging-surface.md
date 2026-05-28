# Teams as your paging surface

Sprint 37 brings Microsoft Teams to parity with the Slack paging surface Sprint 36 shipped. A paged operator can acknowledge, take over, and resolve an incident **without leaving Teams** — adaptive-card actions deep-link straight into OpsMender. This page is the operator-and-admin guide for that surface.

If you only want the tl;dr: **page lands in a Teams chat with three buttons; click Acknowledge; OpsMender does the rest.**

> v1 uses **app-only authentication** (Azure AD client-credentials). No user-delegated OAuth, no per-user consent. Slash-command parity will follow in a later sprint — for v1 the adaptive card buttons are the canonical surface.

---

## 1. What you get

When OpsMender pages you on Teams, the message in the chat carries:

- A bold title with the incident name.
- A FactSet — Priority, Status, Severity (if set), and the OpsMender incident id.
- The first line of the incident description.
- Three Action.Submit buttons — **Acknowledge**, **Take Over**, **Resolve**.
- A **View in OpsMender** `Action.OpenUrl` button (when `OPSMENDER_PUBLIC_URL` is set) that deep-links to `/dashboard/incidents/detail?id=…&from=teams`. The incident detail page surfaces a "you opened this from Teams" breadcrumb so the chat origin is never lost.

Every action click is JWT-verified, routed through the same escalation engine handlers Slack uses, and audited in `incident_pages`.

---

## 2. The buttons

| Button | What it does |
|--------|--------------|
| **Acknowledge** | Pauses the chain and assigns the incident to you. Same as the web Acknowledge button. |
| **Take Over** | Requests reassignment. If the current owner has already acked, they get a 5-minute soft-takeover window to confirm; otherwise the assignment swaps immediately. Admin-only force-takeover stays a web action. |
| **Resolve** | Cancels the escalation chain and marks the incident `resolved`. |
| **View in OpsMender** | Opens the incident detail page with `?from=teams`. |

Action data shape (sent on the `Action.Submit`):

```json
{ "action": "opsmender:ack", "incident_id": "<incident-uuid>" }
```

The `action` strings (`opsmender:ack`, `opsmender:take`, `opsmender:resolve`, `opsmender:view`) are intentionally identical to the Slack ones — the routing helpers are shared.

---

## 3. Setting up the Azure AD app

OpsMender talks to Teams via the Microsoft Graph API using **app-only** auth. You'll create one Azure AD app and grant it both Graph permissions (for outbound DMs) and Bot Framework registration (for inbound card actions).

### 3.1 App registration

1. Azure portal → **App registrations** → **New registration**.
2. Name: `OpsMender Teams`. Single tenant or multi-tenant, your call.
3. Note the **Application (client) ID** and **Directory (tenant) ID** from the Overview page.
4. **Certificates & secrets** → New client secret. Copy the **value** (not the secret id).

### 3.2 Graph permissions (outbound)

Under **API permissions**:

1. Add a permission → **Microsoft Graph** → **Application permissions**.
2. Add `Chat.ReadWrite.All` (needed to post into the user's chat with the bot).
3. Click **Grant admin consent** at the top of the permissions list.

### 3.3 Bot channel registration (inbound)

Sprint 37 step 4 needs a Bot Framework registration so Teams knows where to deliver card-action invokes.

1. Azure portal → **Azure Bot** → Create. Use the same app id as the registration above (re-use the existing app).
2. Under **Configuration**, set the **Messaging endpoint** to `https://<your-opsmender-host>/bot/teams/activity`.
3. Under **Channels**, enable **Microsoft Teams**.

### 3.4 OpsMender connector

In OpsMender → **Notification Channels** → New Teams channel:

| Field | Value |
|-------|-------|
| Tenant ID | The Directory (tenant) ID from §3.1. |
| Application (client) ID | The Application (client) ID. |
| Client secret | The secret value from §3.1. |
| Bot framework app ID | Same client id (or the bot registration's app id if different). |
| Default chat ID | Optional. |

Click **Test connection**. OpsMender runs the OAuth handshake and pings `GET /v1.0/organization` to confirm the credentials are good. A failure here surfaces the Azure error (`invalid_client`, missing scope, …) so you can fix it before going further.

### 3.5 Environment variables

For the per-user `teams_dm_graph` delivery channel, set these in the OpsMender process env:

```
OPSMENDER_TEAMS_GRAPH_TENANT_ID=<tenant-id>
OPSMENDER_TEAMS_GRAPH_CLIENT_ID=<client-id>
OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET=<client-secret>
OPSMENDER_PUBLIC_URL=https://<your-opsmender-host>
```

Missing any of the three Graph env vars → the dispatcher records `delivery_status=skipped` with reason `channel_unconfigured` for `teams_dm_graph` deliveries. The legacy MessageCard `teams_dm` channel (via `OPSMENDER_TEAMS_WEBHOOK_URL`) continues to work independently, so you can migrate at your own pace.

---

## 4. Linking Teams users

Every operator who should be allowed to click adaptive-card actions needs a `bot_user_links` row mapping their Azure AD object id to their OpsMender user id (`POST /bot-connectors/{id}/user-links`). Clickers without a link get a friendly text reply explaining what's missing.

The Azure AD object id is the value that ends up in `activity.from.aadObjectId` on every inbound invoke — Teams's stable identifier for the user.

---

## 5. Verification recipe

1. Make sure your operator user has a `bot_user_links` row (Teams platform user id = your AAD object id) and notification preferences with `teams_dm_graph` enabled + a destination chat id.
2. POST a synthetic page-mode incident: `POST /incidents` with `priority: "P0"` and a service whose chain has at least one step targeting your user.
3. You should receive a Teams message in the configured chat with the adaptive card and three buttons.
4. Click **Acknowledge**. The chain should pause; the OpsMender detail page should show you as the assignee. Within Teams you'll see a short text reply confirming the action landed.
5. Click **Resolve**. Incident status flips to `resolved`, the chain is cancelled, and the audit log records `via=card_action`.

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| "Your Teams account isn't linked" on every click | Missing `bot_user_links` row for your AAD object id in that connector + org. |
| 403 `unauthorized` in OpsMender logs | Bot Framework JWT failed verification: the `bot_app_id` on the Teams connector doesn't match the JWT `aud`, or the JWKS fetch failed (firewall blocking `login.botframework.com`). |
| Test-connection returns `invalid_client` | Client secret value mismatch, or the secret has expired in Azure. |
| Test-connection returns `graph: http 403` | Token exchange worked but `Chat.ReadWrite.All` admin consent wasn't granted. Re-grant in Azure Portal → API permissions. |
| Cards render but buttons do nothing | The bot's **Messaging endpoint** in Azure isn't pointing at `/bot/teams/activity`, or the path is HTTP instead of HTTPS. |
| Dispatcher records `channel_unconfigured` for `teams_dm_graph` | One of the three `OPSMENDER_TEAMS_GRAPH_*` env vars is missing. |

---

See also:

- [Slack as your paging surface](slack-paging-surface.md) — the equivalent guide for Slack.
- [Notification Preferences](notification-preferences.md) — channels, per-priority routing, quiet hours.
- `docs/paging-model.md` — the underlying data model and algorithms.
