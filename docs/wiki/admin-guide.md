This guide covers the core configuration and integration points for administrators managing the OpsMender AI (OpsMender) platform.

For day-to-day user lifecycle operations — invites, password resets, deactivation, soft delete, bootstrap admins, and auth-method badges — see the [People Guide](people-guide.md).

## 0. Multi-tenancy

OpsMender supports multiple isolated organizations on a single deployment. This allows MSPs or large enterprises to host different teams or clients with strict data isolation.

*   **Organizations:** The top-level entity. Every incident, session, and configuration is bound to an organization.
*   **User-Org Mapping:** Users can be members of multiple organizations. Each user has a `primary_org_id` which determines their default context for API requests. The dashboard topbar shows an org switcher when a user belongs to more than one org.
*   **Isolation:** Data is strictly isolated at the database repository layer. Background services and chat bots are organization-aware and only interact with data belonging to their resolved tenant.
*   **Custom Branding:** Each organization can define its own primary/secondary colors, company name, and favicon/logo metadata. Today the runtime branding effect is intentionally narrow: the browser title/favicon can change per tenant, and host-based tenant resolution can carry that identity onto unauthenticated entry points. The shared app accent, dark/light mode, and core dashboard theme remain product-level tokens owned by OpsMender rather than per-org overrides.

### 0.1 Tenant resolution order

For every authenticated request, OpsMender resolves the active organization by checking these in order:

1. **Host header** — if the request hostname is registered for an org under **Organizations → Domains**, that org is *pinned* for the request. The user must be a member or the request is rejected with 403. `X-Forwarded-Host` takes precedence over `Host`, so reverse proxies work correctly.
2. **`X-Org-ID` header** — set automatically by the dashboard when a user picks an org from the topbar switcher. Ignored when the host pins a tenant.
3. **`primary_org_id`** — the persisted default on the user record.

### 0.2 Domain Isolation (host-based routing)

To give each tenant its own URL (`acme.opsmender.example.com`, `globex.opsmender.example.com`):

1. Configure DNS — typically a wildcard `A`/`CNAME` for `*.opsmender.example.com` pointing at the OpsMender deployment, or per-tenant CNAMEs.
2. Make sure your TLS certificate covers the wildcard (Let's Encrypt + DNS-01 challenge or a wildcard cert from your CA).
3. As a global admin, open **Organizations**, click **Domains** on the org you want to pin, and add the hostname. Toggle **Primary** for the canonical URL used in branded links.
4. Verify by visiting `https://<your-host>/tenant/resolve` — `pinned: true` confirms the host is registered.

When a request arrives on a registered domain:
- Non-members of that org are denied with 403, even if their JWT is valid for a different tenant.
- The topbar org switcher is hidden and a `host-pinned` badge is shown instead — there is no ambiguity to resolve.
- Branding is applied based on the host even on the unauthenticated `/login` and `/register` pages (via the public `GET /tenant/resolve` endpoint).

If you run OpsMender on a single hostname, you can skip Domain Isolation entirely — the X-Org-ID + primary_org_id flow keeps working as before.

## 1. Authentication

OpsMender supports three auth paths:

- **Local username/password.** First user registered automatically becomes the global `admin`. Use this for the initial bootstrap and as a break-glass account.
- **Per-tenant SSO (OIDC).** Each org can wire its own identity provider — Okta, Azure AD, Google Workspace, Auth0, or Keycloak. OpsMender redirects users to the IdP and JIT-provisions accounts on first login.
- **Per-tenant SSO (SAML 2.0).** Each org can wire a SAML IdP such as Okta classic apps, Azure AD enterprise apps, or ADFS. OpsMender acts as the SP and JIT-provisions users on first successful assertion.

### 1.1 Configuring SSO for an org (OIDC)

Open **Organizations** as a global admin, click **SSO** on the target org, then fill in:

- **Discovery URL** — the IdP's `.well-known/openid-configuration`. Examples:
    - Okta: `https://{your-domain}.okta.com/.well-known/openid-configuration`
    - Azure AD: `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration`
    - Google: `https://accounts.google.com/.well-known/openid-configuration`
    - Keycloak: `https://{host}/realms/{realm}/.well-known/openid-configuration`
- **Client ID / Client Secret** — register OpsMender as an application in your IdP; the redirect URI to whitelist there is `https://{your-tenant-host}/auth/sso/{org-slug}/callback`.
- **Scopes** — defaults to `openid email profile`. Add extras (e.g. `groups`) if your IdP requires them.
- **Email / Name claim** — change only if your IdP uses non-standard claim names.
- **Default role** — role assigned to *new* users JIT-provisioned through SSO. Existing users keep their current role.
- **Allowed email domains** — optional comma-separated allowlist. Mismatched domains are rejected with 403, even if the IdP authenticated them. Use this when one IdP serves multiple orgs.
- **Enabled** — uncheck to temporarily disable without losing the config.

When SSO is enabled, the login page on a host-pinned domain shows a "Sign in with {Org name}" button above the local form. Local login still works (and remains the only option on hosts that aren't pinned).

The client secret is encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). The encryption key derives from `OPSMENDER_SECRET_KEY` (preferred) or falls back to `OPSMENDER_JWT_SECRET`. Set a dedicated `OPSMENDER_SECRET_KEY` in production so rotating the JWT secret doesn't invalidate every stored SSO secret.

### 1.2 Configuring SAML for an org

Open **Organizations** as a global admin, click **SAML** on the target org, then fill in:

- **Metadata URL** or **Raw metadata XML** — exactly one is required. Use the URL when your IdP exposes stable metadata; paste XML when it does not.
- **Default role** — role assigned to new JIT-provisioned users.
- **Allowed email domains** — optional comma-separated allowlist enforced after the assertion is validated.
- **Enabled** — uncheck to temporarily disable SAML without losing the config.

OpsMender's SP-side keypair is global and comes from env:

- `OPSMENDER_SAML_SP_CERT`
- `OPSMENDER_SAML_SP_KEY`
- optional `OPSMENDER_SAML_SP_ENTITY_ID`

Generate a self-signed keypair with:

```bash
opsmender saml gen-sp-keys --cn opsmender-sp --days 3650
```

The IdP admin will usually need these two URLs:

- **ACS / Reply URL:** `https://{your-tenant-host}/auth/saml/{org-slug}/acs`
- **SP metadata:** `https://{your-tenant-host}/auth/saml/{org-slug}/metadata`

On successful login, OpsMender validates the signed SAML response, extracts the email/name attributes, JIT-provisions the user if needed, and redirects back through the same `#sso_token=...` handoff used by OIDC.

## 2. Runtime Configuration

You can manage runtime configurations via the **Config** tab in the dashboard.
These settings apply globally to the OpsMender instance.

Key configurations include:
- **Default Tier:** The default safety tier for new sessions (e.g., Tier 2).
- **Auto-Start Policies:** Conditions under which OpsMender will automatically start an AI session upon incident ingestion.
- **SLA Poller Defaults:** The non-AI Reliability checker that repeatedly probes HTTP/TCP targets. HTTP targets can treat exact codes, status classes (`2xx`), ranges (`200-299`), or expected error codes such as `404` as healthy.

## 3. Model Configuration

OpsMender supports multiple LLM providers. Navigate to **Models** in the sidebar (`/dashboard/models`) to configure them.

Supported providers as of Sprint 62:

| Provider | Notes |
|----------|-------|
| **Anthropic** | Claude models. Set `ANTHROPIC_API_KEY` in `.env`. |
| **OpenAI** | GPT models. Set `OPENAI_API_KEY` in `.env`. |
| **Azure OpenAI** | Azure-hosted OpenAI deployments. Requires `base_url` (your resource endpoint) and `api_version`. |
| **AWS Bedrock** | Uses the native AWS credential chain. Requires an AWS Region; optional AWS Profile name if you want a specific shared-config profile. No raw API key is stored in OpsMender. |
| **Ollama** | Local runtime. Default `base_url` is `http://localhost:11434`. No API key required. |
| **OpenAI-compatible** | Any OpenAI-API-shape endpoint: vLLM, LM Studio, OpenRouter, Together, Groq, Fireworks, Anyscale, and most local OpenAI-shape runtimes. **Requires** `base_url`; API key is optional (some local endpoints accept any string or none). |

Per Sprint 62 design, OpsMender stores only the **environment variable name** for each provider's secret, never the raw value. Set the actual key in `.env` and reference it from the dashboard. Cloud providers use native credential discovery instead of long-lived pasted secrets: AWS Bedrock uses the AWS credential chain now, and GCP Vertex AI will use ADC in the next step.

To add a model config:

1. Click **New model config**.
2. Select your provider — only the fields that provider needs are shown (e.g. OpenAI-compatible shows Base URL; Azure OpenAI shows both Base URL and API Version; Bedrock shows AWS Region + optional AWS Profile).
3. For Bedrock, enter the AWS Region first, then click **Refresh Catalog** if you want the live Bedrock model list for that region/profile.
4. Pick a model from the discovered catalog, or click **Type manual model ID** if discovery is unavailable or the model isn't reported (e.g. a proxy that doesn't implement `/v1/models`).
5. Save. Model discovery is cached for 60 seconds for local/proxy endpoints and 1 hour for cloud catalogs so the page stays snappy.

## 4. MCP Servers and Skills

OpsMender uses the Model Context Protocol (MCP) to interact with your infrastructure. MCP servers and Skills are managed separately: MCP servers define the connection, while Skills define the allowed operations for that connection.

1. Go to **Config** > **MCP** to add or test an MCP server.
2. Provide the command or transport details for the MCP server (stdio, SSE, or HTTP).
3. Go to **Skills** to import, edit, clone, or bind `SKILL.md` content to an MCP server.
4. Use tiers and Skill classifications together to control what OpsMender can execute.

## 5. Chat Bot Connectors

External chat bot connectors are managed in **Config** > **Integrations**.

1. Click **Add Connector**.
2. Choose the platform (e.g., `telegram`, `slack`, `discord`, `teams`, `mattermost`, `matrix`, `whatsapp`, `signal`, `lark`, `dingtalk`, `wecom`, `twilio`, `email`).
3. Add platform-specific connector settings as JSON.
4. Add credentials as `key=value` lines (e.g., `bot_token=...`).
5. Select allowed capabilities (e.g., `incident_lookup`, `session_status`, `approvals`, `notifications`).
6. Click **Test** to validate the saved configuration.

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
5. Configure your external tool to send a webhook POST request to `https://<your-opsmender-url>/incidents/ingest` with the header `X-OpsMender-Token: <your-token>`.
