# Auth Guide (default — single workspace, email + invite)

This is the default OpsMender auth model: one workspace, email + password, admin-issued invites, three roles (admin / operator / viewer). It's what 95% of self-hosted installs run on, and it's what a fresh `docker run` lands you in.

If you need **SSO (OIDC) or SAML**, those features are still in the product — they live in Settings for the single workspace so the default install stays simple. See [Advanced Auth Guide](advanced-auth-guide.md).

> **Companion guide:** [People Guide](people-guide.md) covers day-to-day People-page operations (invites, password resets, soft delete, etc.) in detail. This page is the conceptual auth model — who can sign in, how accounts are created, what changes when you opt in to advanced features.

---

## The default flow

1. **First admin** is created during install via two env vars (the "bootstrap admin" pattern). After this, public self-signup is closed.
2. **All other users arrive via invite.** Admins open `/dashboard/people` and create an invite for each user, choosing the role at invite time.
3. **Users accept invites** at a one-time URL, pick a password, and they're signed in.
4. **Admins manage users** from the People page — change role, deactivate, reset password, soft-delete.
5. **Roles gate behavior** — `admin` can do everything, `operator` can run incident response + change runtime state, `viewer` is read-only.

That's the entire default model. No tenant picker, no org switcher in the TopBar.

---

## Bootstrap admin (first-run setup)

On first start, when the users table is empty and both of these env vars are set, OpsMender creates the first admin user automatically:

```dotenv
OPSMENDER_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD=replace-me-on-first-login
```

After this row exists, public self-signup is closed — the only path to a new account is an admin invite.

If you skip the bootstrap env vars, the first user to register via `/register` becomes the admin (legacy escape hatch). Most production installs should set the env vars and never leave self-signup open.
Public registration asks only for email + password; OpsMender derives a
collision-safe username for compatibility with historical attribution.

---

## Roles

OpsMender ships three roles:

| Role | What they can do |
|------|------------------|
| **admin** | Everything: manage users + invites, change runtime config, configure MCP servers + skills, manage paging surfaces, view audit log, approve / reject Tier 1 actions, run incident sessions. |
| **operator** | Run incident sessions, approve / reject Tier 1 actions, change runtime config, manage MCP/skills/paging surfaces. Cannot manage users / invites. |
| **viewer** | Read-only access to incidents, sessions, audit log, and dashboards. Cannot create incidents, run sessions, or change config. |

Role gating happens both in the UI (button visibility) and in the backend (`require_role()` dependency on every mutating route). The UI side is convenience; the backend side is the actual security boundary.

Roles are assigned at **invite time** by the admin. They can be changed later from the per-user detail page (`/dashboard/people/detail?id=…`).

---

## Sign-in surface

The default login page shows:

```text
Email
Password
[Sign in]
```

No SSO/SAML buttons until a provider is configured. No org switcher. No tenant picker.

The Register link only appears when self-signup is still open (i.e. no users exist yet). Once an admin exists, the link is hidden and that route returns 403.

---

## Multi-factor authentication

Local accounts can enable TOTP from **Profile & Settings → Multi-factor
authentication**:

1. Scan the QR code or enter the manual key in an authenticator app.
2. Enter the current six-digit code.
3. Save the eight recovery codes shown after confirmation. Each works once and
   cannot be displayed again.

Future password logins pause at `/mfa-challenge` until an authenticator or
recovery code is verified. Disabling MFA also requires a current factor.

Admins can enable **Require MFA for this organization** in the same security
panel. Local users without MFA are sent to `/mfa-setup` after password login.
OIDC and SAML users continue to follow their identity provider's MFA policy.

---

## Inviting users

Default-mode invite flow (full details in [People Guide §4](people-guide.md)):

1. Sign in as admin → open `/dashboard/people` → click **Invites** tab.
2. **New invite** for one person, or **Bulk import** for a list.
3. Enter the recipient email + the role to grant on accept.
4. OpsMender returns a **one-time invite URL**. If SMTP is configured (see below), OpsMender also tries to email it; if not, copy the URL and deliver it however you want (chat, your own email, etc.).
5. The recipient opens the URL, picks a username + password, and is signed into OpsMender as the role you chose.

Invites are single-use and expire after **72 hours**. Lost or expired? Use the
**resend** action on a pending row — it revokes the old token and mints a fresh
one.

After sign-in, dashboard links use plain `/dashboard/...` paths. The instance
has one active workspace, so there is no org slug in dashboard URLs.

---

## Password resets

Admins reset other users' passwords from the per-user detail page:

1. `/dashboard/people` → click a user → **Reset password**.
2. OpsMender mints a one-time reset URL. If SMTP is configured the user gets an email; if not, copy the URL and send it manually.
3. The recipient opens the URL, picks a new password, and signs in.

Users cannot self-trigger a password reset in v1 — it goes through an admin. This is the same "single break-glass channel" pattern as invites and matches the simple-by-default posture.

---

## Optional SMTP delivery

SMTP is purely a delivery convenience. **The product works without it** because the one-time invite + reset URLs are always returned to the admin in the UI for manual delivery.

If you do want automatic email delivery:

```dotenv
OPSMENDER_SMTP_HOST=smtp.example.com
OPSMENDER_SMTP_PORT=587
OPSMENDER_SMTP_USER=opsmender
OPSMENDER_SMTP_PASSWORD=...
OPSMENDER_SMTP_FROM=opsmender@example.com
OPSMENDER_SMTP_USE_TLS=true
```

Also set this so links in emails point at the right host:

```dotenv
OPSMENDER_PUBLIC_BASE_URL=https://opsmender.example.com
```

The invite-created and reset-mint modals always tell you which delivery path happened: **Sent** / **Failed** / **Not configured**. SMTP failure is logged but never blocks the operation — the URL is always available.

---

## Soft delete vs deactivate

| Action | When to use | Reversible? |
|--------|-------------|-------------|
| **Deactivate** | User is on leave, has been suspended, or shouldn't be able to sign in right now. Their history stays clickable; they keep showing up in user pickers as inactive. | Yes — reactivate from the per-user page. |
| **Soft delete** | User has left the org. Login is blocked, sensitive fields are scrubbed, but historical attribution (audit entries, incident assignments, etc.) is preserved. | No. |

Soft delete has prerequisites that the People page walks you through (deactivated + zero roster memberships). The detail walkthrough is in [People Guide §6](people-guide.md).

---

## Optional SSO and SAML

OpsMender ships one env flag that exposes the optional SSO/SAML setup surface in
a fresh install:

```dotenv
OPSMENDER_ADVANCED_AUTH_ENABLED=false    # default
```

| Flag | When `true` |
|------|-------------|
| `OPSMENDER_ADVANCED_AUTH_ENABLED` | Settings surfaces the workspace **OIDC SSO** and **SAML** forms so an admin can wire up an IdP. |

The flag is a **visibility hint only**:

- The SSO/SAML runtime routes (`/auth/sso/...`, `/auth/saml/...`) work regardless of the flag.
- **An org with an already-configured SSO or SAML provider keeps its admin settings visible even when `advanced_auth_enabled=false`.** Settings never silently disappear when you flip a flag off. That's the explicit D-027 rule.

Set up details and operator flow live in [Advanced Auth Guide](advanced-auth-guide.md).

---

## Related guides

- [People Guide](people-guide.md) — day-to-day People-page operations: invites, password resets, soft delete, troubleshooting matrix.
- [Advanced Auth Guide](advanced-auth-guide.md) — SSO (OIDC), SAML, and custom-domain login behavior.
- [Getting Started](getting-started.md) — first local boot and first login.
- [Administrator Guide](admin-guide.md) — everything else: runtime config, MCP, integrations, models.
