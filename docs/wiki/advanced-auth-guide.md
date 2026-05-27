# Advanced Auth Guide (OIDC, SAML, multi-tenancy)

This guide covers OpsMender's optional auth surfaces — per-tenant SSO (OIDC), per-tenant SAML 2.0, multi-tenant organizations, and host-based domain isolation. All of these stay in the codebase regardless of how you configure them, but they're hidden by default so a self-hosted install lands on the simpler email + invite flow.

If you're running a single workspace and just need email + invite + password reset, you don't need this page — see [Auth Guide](auth-guide.md) and [People Guide](people-guide.md).

---

## The two visibility flags

OpsMender uses two env flags to gate the advanced-auth UI surfaces. Both default to `false`.

```dotenv
OPSMENDER_MULTI_ORG_ENABLED=false
OPSMENDER_ADVANCED_AUTH_ENABLED=false
```

### `OPSMENDER_MULTI_ORG_ENABLED`

When `true`:

- Sidebar Admin entry reads **Organizations** (default reads **Workspace Settings**).
- TopBar shows the org switcher dropdown when the user belongs to more than one org.
- "New Organization" / "Create Organization" buttons appear on the Organizations page.
- Per-row Delete action becomes available.

The backend organization model is always there — every entity is `org_id`-scoped (D-008). The flag only changes the UI presentation.

### `OPSMENDER_ADVANCED_AUTH_ENABLED`

When `true`:

- Organizations / Workspace Settings page surfaces per-org **SSO** and **SAML** action buttons.

### The "settings never silently disappear" rule

For both flags, the rule is:

> The visibility flag is for **enabling fresh setup**. Once a provider is configured, its settings stay visible regardless of the flag.

Concretely: the disjunction `advanced_auth_enabled || sso_configured || saml_configured` decides whether the SSO + SAML buttons render on the Organizations page. `sso_configured` and `saml_configured` are per-tenant DB lookups (rows in `org_sso_configs` and `org_saml_configs`).

So if you set up SSO for a tenant, decide later to flip `OPSMENDER_ADVANCED_AUTH_ENABLED=false`, and restart — the SSO settings are still reachable for that tenant. Operators don't get locked out of editing their existing provider.

**Runtime auth routes** (`/auth/sso/...`, `/auth/saml/...`) keep working regardless of either flag. The flags are UI-visibility hints, not auth-disable switches.

---

## Multi-tenancy

OpsMender supports multiple isolated organizations on a single deployment. Useful for MSPs hosting different clients, or large enterprises with strict data isolation between teams.

### Concepts

- **Organization** — the top-level entity. Every incident, session, configuration, ingest token, MCP server, model config, etc. is bound to an organization.
- **User-Org membership** — users can belong to multiple organizations. Each user has a `primary_org_id` that determines their default context for API requests.
- **Isolation** — strictly enforced at the database repository layer. Background services (bot dispatcher, SLA poller, session runner) are organization-aware.
- **Per-org branding** — each org can carry its own browser title / favicon for host-pinned tenants. The shared app accent + dashboard theme remain product-level tokens, not per-org overrides.

### Tenant resolution order

For every authenticated request:

1. **Host header** — if the request hostname is registered for an org under **Organizations → Domains**, that org is *pinned*. Non-members get 403. `X-Forwarded-Host` takes precedence over `Host` for reverse-proxy compatibility.
2. **`X-Org-ID` header** — set automatically by the dashboard when a user picks an org from the TopBar switcher. Ignored when the host pins a tenant.
3. **`primary_org_id`** — the persisted default on the user record.

### Domain Isolation (host-based routing)

To give each tenant its own URL (`acme.opsmender.example.com`, `globex.opsmender.example.com`):

1. **DNS** — typically a wildcard `A`/`CNAME` for `*.opsmender.example.com` pointing at the OpsMender deployment, or per-tenant CNAMEs.
2. **TLS** — make sure your certificate covers the wildcard (Let's Encrypt + DNS-01 challenge or a wildcard cert from your CA).
3. **Register the host** — as a global admin, open **Organizations**, click **Domains** on the org you want to pin, and add the hostname. Toggle **Primary** for the canonical URL used in branded links.
4. **Verify** — visit `https://<your-host>/tenant/resolve` — `pinned: true` confirms the host is registered.

When a request lands on a pinned host:

- Non-members of that org get 403, even if their JWT is valid for a different tenant.
- The TopBar org switcher is hidden and a `host-pinned` badge is shown instead.
- Branding is applied based on the host even on the unauthenticated `/login` and `/register` pages via the public `GET /tenant/resolve`.

If you run OpsMender on a single hostname, you can skip Domain Isolation entirely — the `X-Org-ID` + `primary_org_id` flow keeps working.

---

## SSO (OIDC) per tenant

OpsMender acts as an OIDC relying party. Each org can wire its own IdP — Okta, Azure AD, Google Workspace, Auth0, Keycloak, or anything that exposes a discovery URL.

### Setup

1. Set `OPSMENDER_ADVANCED_AUTH_ENABLED=true` and restart so the **SSO** action buttons appear on the Organizations page.
2. Sign in as global admin → **Organizations** → click **SSO** on the target org.
3. Fill in:

| Field | What it is |
|-------|------------|
| **Discovery URL** | The IdP's `.well-known/openid-configuration` endpoint. See examples below. |
| **Client ID** | Issued by your IdP when you register OpsMender as an application. |
| **Client Secret** | Same place. Encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). |
| **Scopes** | Defaults to `openid email profile`. Add `groups` etc. if your IdP requires them. |
| **Email / Name claim** | Defaults to `email` / `name`. Change only if your IdP uses non-standard claim names. |
| **Default role** | Role assigned to *new* JIT-provisioned users. Existing users keep their current role. |
| **Allowed email domains** | Optional comma-separated allowlist. Mismatched domains get 403, even if authentication succeeded. Useful when one IdP serves multiple orgs. |
| **Enabled** | Uncheck to temporarily disable without losing the config. |

4. Register OpsMender as an application in your IdP. The **redirect URI** to whitelist there is:

   ```
   https://{your-tenant-host}/auth/sso/{org-slug}/callback
   ```

### Discovery URL examples

| IdP | URL |
|-----|-----|
| Okta | `https://{your-domain}.okta.com/.well-known/openid-configuration` |
| Azure AD | `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration` |
| Google Workspace | `https://accounts.google.com/.well-known/openid-configuration` |
| Keycloak | `https://{host}/realms/{realm}/.well-known/openid-configuration` |
| Auth0 | `https://{your-tenant}.auth0.com/.well-known/openid-configuration` |

### Login flow

When SSO is enabled on a host-pinned domain, the login page shows a **Sign in with {Org name}** button above the local email/password form. Local login still works (and remains the only option on hosts that aren't pinned). The button only appears when a provider is actually configured for the resolved tenant — that gating is independent of the env flag.

### Client secret encryption

Client secrets are encrypted at rest with Fernet. The encryption key derives from `OPSMENDER_SECRET_KEY` (preferred) or falls back to `OPSMENDER_JWT_SECRET`.

**Set a dedicated `OPSMENDER_SECRET_KEY` in production** so rotating the JWT secret doesn't invalidate every stored SSO secret.

---

## SAML 2.0 per tenant

OpsMender acts as a SAML SP. Each org can wire a SAML IdP — Okta classic apps, Azure AD enterprise apps, ADFS, anything that speaks SAML 2.0.

### One-time SP keypair setup

OpsMender's SP-side keypair is global (shared across all tenants) and comes from env:

```dotenv
OPSMENDER_SAML_SP_CERT=<base64-encoded PEM cert>
OPSMENDER_SAML_SP_KEY=<base64-encoded PEM private key>
# Optional — defaults to https://{host}/auth/saml/{org-slug}/metadata
OPSMENDER_SAML_SP_ENTITY_ID=
```

Generate a self-signed keypair with:

```bash
opsmender saml gen-sp-keys --cn opsmender-sp --days 3650
```

### Per-tenant setup

1. Set `OPSMENDER_ADVANCED_AUTH_ENABLED=true` so the **SAML** action buttons appear.
2. Sign in as global admin → **Organizations** → click **SAML** on the target org.
3. Provide **exactly one** of:
   - **Metadata URL** — when your IdP exposes stable metadata. OpsMender fetches and caches it.
   - **Raw metadata XML** — paste the XML directly when the IdP doesn't expose a URL.
4. Fill in:
   - **Default role** — assigned to new JIT-provisioned users.
   - **Allowed email domains** — optional comma-separated allowlist, enforced after the assertion is validated.
   - **Enabled** — uncheck to temporarily disable.

The IdP admin will need these two URLs:

- **ACS / Reply URL:** `https://{your-tenant-host}/auth/saml/{org-slug}/acs`
- **SP metadata:** `https://{your-tenant-host}/auth/saml/{org-slug}/metadata`

### Login flow

On successful login, OpsMender validates the signed SAML response, extracts the email/name attributes, JIT-provisions the user if needed, and redirects back through the same `#sso_token=...` handoff used by OIDC.

### Deferred features

- **SLO (Single Logout)** — not implemented.
- **Encrypted assertions** — not implemented. Signed assertions are validated.

Both stay deferred to a later sprint unless a customer asks.

---

## Operating patterns

A clean pattern for teams that adopt SSO:

1. Keep **one local admin account** as break-glass. Document who has its password.
2. Configure **OIDC or SAML per org** for normal user login.
3. Use the **People page** to manage roles, activation state, and offboarding — JIT-provisioned users still show up there with the right auth-method badge.
4. Use **invites** for local users only when there's a real reason not to use SSO (contractor with no IdP account, etc.).
5. **Allowed email domains** — set this on the SSO/SAML config when one IdP serves multiple orgs to avoid cross-tenant identity bleed.

Day-to-day identity stays in your IdP; the OpsMender-native admin path stays available for recovery.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **SSO** / **SAML** buttons not visible on the Organizations page | `OPSMENDER_ADVANCED_AUTH_ENABLED=false` and no provider is yet configured for any tenant | Set `OPSMENDER_ADVANCED_AUTH_ENABLED=true` and restart. The buttons appear; configure a provider; the flag becomes optional after that. |
| Existing SSO config disappeared after flipping the flag off | Should not happen — verify | The `sso_configured` per-tenant lookup keeps settings visible even when the flag is off. If they're gone, there's a real bug; capture the `/config` response and the `org_sso_configs` row. |
| TopBar org switcher not visible despite multiple orgs | `OPSMENDER_MULTI_ORG_ENABLED=false` | Set it to `true` and restart. Host-pinned tenants intentionally hide the switcher regardless of this flag. |
| Login page does not show "Sign in with {Org name}" | The host isn't pinned, or no provider is configured for the resolved tenant | Register the host under **Organizations → Domains**, then verify with `GET /tenant/resolve`. |
| SSO callback returns 403 | User's email matches but they're not a member of the org | OpsMender JIT-provisions on first login, but `allowed_email_domains` can also reject. Check the SSO config's allowlist. |
| SAML assertion validation fails | Clock skew between IdP and OpsMender, or wrong SP cert | Verify system time on the OpsMender host; verify `OPSMENDER_SAML_SP_CERT` matches what the IdP has. |
| Rotated `OPSMENDER_JWT_SECRET` and now SSO logins fail with "decryption error" | Stored OIDC client secrets were encrypted using the old derived key | Set a dedicated `OPSMENDER_SECRET_KEY` to decouple SSO secret encryption from JWT secret rotation. Re-save each SSO config to re-encrypt under the new key. |

---

## Related guides

- [Auth Guide](auth-guide.md) — default email + invite flow, bootstrap admin, three roles.
- [People Guide](people-guide.md) — day-to-day People-page operations.
- [Administrator Guide](admin-guide.md) — runtime config, MCP, integrations, models.
- [Getting Started](getting-started.md) — first local boot.
