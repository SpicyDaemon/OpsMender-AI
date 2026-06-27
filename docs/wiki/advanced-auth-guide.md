# Advanced Auth Guide (OIDC and SAML)

This guide covers OpsMender's optional identity-provider setup for the single
workspace: OIDC SSO, SAML 2.0, and custom-domain login behavior.

If you only need email + invite + password reset, use [Auth Guide](auth-guide.md)
and [People Guide](people-guide.md).

---

## Visibility Flag

The setup forms live in **Settings -> Workspace**. A fresh install can expose
them with:

```dotenv
OPSMENDER_ADVANCED_AUTH_ENABLED=true
```

The flag is only a setup visibility hint:

- Runtime routes (`/auth/sso/...`, `/auth/saml/...`) keep working regardless.
- If OIDC or SAML is already configured, Settings keeps showing the forms even
  when the flag is later turned off.
- The app still has one active workspace. There is no org switcher and no
  `/org/<slug>/dashboard/...` URL scope.

---

## Custom Domains

The workspace can register one or more hostnames under
**Settings -> Workspace -> Custom domains**. `GET /tenant/resolve` uses those
domains to show the workspace name, branding, and SSO/SAML login buttons before
the user authenticates.

Custom domains do not switch authenticated API context. Signed-in requests use
the user's `primary_org_id`, which points at the single workspace.

---

## OIDC SSO

OpsMender acts as an OIDC relying party. Common IdPs include Okta, Azure AD,
Google Workspace, Auth0, and Keycloak.

1. Set `OPSMENDER_ADVANCED_AUTH_ENABLED=true` and restart if the forms are not
   already visible.
2. Sign in as admin and open **Settings -> Workspace -> OIDC SSO**.
3. Fill in the discovery URL, client ID, client secret, scopes, claim names,
   default role, allowed email domains, and active toggle.
4. Register this redirect URI in the IdP:

   ```text
   https://{your-host}/auth/sso/{workspace-slug}/callback
   ```

Discovery URL examples:

| IdP | URL |
|-----|-----|
| Okta | `https://{your-domain}.okta.com/.well-known/openid-configuration` |
| Azure AD | `https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration` |
| Google Workspace | `https://accounts.google.com/.well-known/openid-configuration` |
| Keycloak | `https://{host}/realms/{realm}/.well-known/openid-configuration` |
| Auth0 | `https://{your-tenant}.auth0.com/.well-known/openid-configuration` |

Client secrets are encrypted at rest with Fernet. Set a dedicated
`OPSMENDER_SECRET_KEY` in production so JWT secret rotation does not invalidate
stored SSO secrets.

---

## SAML 2.0

OpsMender acts as a SAML SP. IdP metadata can be supplied as a URL or pasted raw
XML.

First configure the global SP keypair:

```dotenv
OPSMENDER_SAML_SP_CERT=<base64-encoded PEM cert>
OPSMENDER_SAML_SP_KEY=<base64-encoded PEM private key>
# Optional; defaults to https://{host}/auth/saml/{workspace-slug}/metadata
OPSMENDER_SAML_SP_ENTITY_ID=
```

Generate a self-signed keypair with:

```bash
opsmender saml gen-sp-keys --cn opsmender-sp --days 3650
```

Then open **Settings -> Workspace -> SAML** and configure metadata, attribute
mapping, default role, allowed domains, signature requirements, and active
state.

The IdP admin needs:

- **ACS / Reply URL:** `https://{your-host}/auth/saml/{workspace-slug}/acs`
- **SP metadata:** `https://{your-host}/auth/saml/{workspace-slug}/metadata`

Deferred features:

- SLO (Single Logout)
- Encrypted assertions

---

## Login Flow

When a custom domain resolves to the workspace and a provider is active, the
login page shows the matching **Sign in with {Workspace}** button. On an
unregistered host, the email field can still discover matching OIDC/SAML
providers through `POST /auth/sso-hint`.

Local email/password login remains available for break-glass admins.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| OIDC/SAML forms are not visible | `OPSMENDER_ADVANCED_AUTH_ENABLED=false` and no provider exists yet | Set it to `true` and restart. |
| Existing provider settings disappeared | Should not happen | Check `/config` for `sso_configured` / `saml_configured` and verify the DB row exists. |
| Login page does not show the provider button | Host is not registered or provider is inactive | Add the host under Custom domains, then verify `GET /tenant/resolve`. |
| SSO callback returns 403 | Email domain is rejected | Check the provider's allowed email domains. |
| SAML validation fails | Clock skew or wrong SP cert | Verify host time and the IdP's configured certificate. |
| Rotating JWT secret broke OIDC secret decrypt | Secrets used the old derived key | Set `OPSMENDER_SECRET_KEY` and re-save the OIDC config. |

---

## Related Guides

- [Auth Guide](auth-guide.md) - default email + invite flow.
- [People Guide](people-guide.md) - invites, password resets, soft delete.
- [Administrator Guide](admin-guide.md) - runtime config, MCP, integrations,
  models.
