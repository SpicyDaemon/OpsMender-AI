# People Guide

This guide covers the **Admin → People** surface in OpsMender: inviting users, understanding how they authenticate, sending password resets, and safely deactivating or deleting accounts.

The short version:

- Use **Invites** when you want a new human in the system.
- Use **Organizations → SSO / SAML** when you want identity-provider login for an org.
- Use the **Auth method** badge on each user to see whether they are local, OIDC-backed, or SAML-backed.
- Soft delete is intentionally gated: the account must already be deactivated and removed from every on-call roster.

---

## 1. Who should read this

This page is for admins managing human access to OpsMender.

- **Admins** can invite users, change roles, deactivate users, mint password-reset links, and soft-delete users.
- **Operators** and **viewers** can use the product, but they do not manage other users from the People surface.

If you are wiring identity providers, also read the [Administrator Guide](admin-guide.md). If you are configuring paging destinations for an existing user, see [Notification Preferences](notification-preferences.md).

---

## 2. Where the People surface lives

Open **Admin → People** in the sidebar.

There are two tabs:

- **Users** — everyone who already has an OpsMender account in your current organization.
- **Invites** — pending, accepted, expired, or revoked invite links for that organization.

The People surface is organization-scoped. If your account belongs to more than one org, the active org in the topbar switcher determines which users and invites you see.

---

## 3. Authentication methods

Each user row shows an **Auth method** badge:

- **Local** — the user signs in with a username and password managed by OpsMender. This is the default in single-workspace installs.
- **OIDC** — the user was provisioned through the org's OpenID Connect provider, shown as `oidc:<org-slug>`. Only appears when OIDC is configured for the tenant.
- **SAML** — the user was provisioned through the org's SAML 2.0 provider, shown as `saml:<org-slug>`. Only appears when SAML is configured for the tenant.

Clicking an **OIDC** or **SAML** badge deep-links into **Workspace Settings** and opens the matching auth configuration modal. That is the fastest way to answer "which IdP owns this user?"

For the default install (local auth only), every row reads **Local** and no IdP setup is required. See [Auth Guide](auth-guide.md) for the default model.

If you want OIDC or SAML, see [Advanced Auth Guide](advanced-auth-guide.md). Notes that apply once it's wired up:

- OIDC and SAML users are **JIT-provisioned** on first successful login.
- Existing users keep their current role when they come back through SSO/SAML; the login path only updates the recorded auth source.
- Local login remains useful as a break-glass path for at least one admin account.

---

## 4. Adding a user

Use the **Invites** tab instead of creating users directly.

1. Open **Admin → People → Invites**.
2. Use **New invite** for one person, or **Bulk import** when you already have a list.
3. For a single invite, enter the recipient email and the OpsMender role to grant on accept.
4. For bulk import, paste one line per recipient in this format:

```text
alice@example.com, operator
bob@example.com, viewer
carol@example.com, admin
```

5. Submit the form.
6. OpsMender returns one-time invite URLs. Copy them immediately if you need to deliver them manually.

The invited user opens `/invite?token=...`, picks a username and password, and is then signed into OpsMender.

Invite behavior:

- Invites are **single-use**.
- Invites expire after **72 hours**.
- Revoked, expired, and already-used tokens are all rejected on consume.
- The invite list shows the derived state so admins can tell pending vs accepted vs expired vs revoked at a glance.
- If the recipient loses the original URL, use the **resend** action on the pending invite row. OpsMender revokes the old link and mints a fresh one in one step.
- Bulk import is **best effort per line**. OpsMender creates the valid invites, reports any failed lines separately, and still shows every newly minted URL exactly once.

### SMTP vs manual delivery

If SMTP is configured, OpsMender also attempts to email the invite automatically.

The invite-created modal always tells you which path happened:

- **Sent** — SMTP succeeded.
- **Failed** — SMTP was configured, but delivery failed. Copy the URL and send it manually.
- **Not configured** — no SMTP settings were present, so manual delivery is expected.

---

## 5. Password resets

For an existing local user:

1. Open **Admin → People → Users**.
2. Click **Manage** on the user.
3. Click **Send password reset**.
4. Copy the one-time reset URL from the modal if needed.

Password-reset links are best-effort email plus guaranteed copy-paste output, same as invites.

Important boundaries:

- Reset links are for **local** credentials.
- OIDC and SAML users normally reset their password in the external identity provider, not in OpsMender.
- The People page still lets you inspect those users and their role/status, but their credential lifecycle is owned by the IdP.

---

## 6. Deactivate vs soft delete

OpsMender separates "do not let this user log in" from "remove this account from active use."

### Deactivate

Use **Deactivate** when you want to block future login but preserve the account.

Effects:

- The user cannot sign in.
- Historical references remain intact.
- The account can be reactivated later.

This is the normal first step for offboarding or temporary suspension.

### Soft delete

Use **Delete** only after the account is truly no longer needed.

OpsMender enforces two preconditions before the delete button enables:

1. The account must already be **deactivated**.
2. The user must be removed from **all on-call rosters**.

That roster check is deliberate. Deleting an on-call user while they are still in a rotation would corrupt the operational surface more than it helps cleanup.

Soft delete behavior:

- The user is hidden from normal active-user workflows.
- The stored email is scrubbed.
- The password hash is cleared.
- The username is preserved so historical incidents, audit rows, and assignments still point to something human-readable.
- Clicking an old user link lands on the dedicated **User no longer in OpsMender** placeholder.

---

## 7. Bootstrap and first-admin setup

OpsMender supports two bootstrap paths:

### Fresh install, no users yet

On an empty deployment, the first successful local registration becomes the initial admin.

### Env-driven bootstrap admin

For unattended installs, set:

```dotenv
OPSMENDER_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD=change-me
```

When OpsMender starts and finds zero users, it creates the bootstrap admin automatically. If there are zero organizations too, it also creates the initial org.

This is the preferred path for production automation because it removes the need to leave self-registration open.

---

## 8. Workspace context

OpsMender runs one active workspace per instance. The People surface targets
that workspace, and there is no org switcher or invite org picker to manage.

Auth-method badges still show the source that actually provisioned the user
(`local`, `oidc:<slug>`, or `saml:<slug>`). Clicking an OIDC/SAML badge takes
admins to **Settings -> Workspace**, where the provider is configured.

---

## 9. SMTP and public URL settings

OpsMender uses SMTP only as a convenience layer. Invites and password resets still work without it because the UI always shows the one-time URL.

Relevant env vars:

```dotenv
OPSMENDER_PUBLIC_BASE_URL=https://opsmender.example.com

OPSMENDER_SMTP_HOST=smtp.example.com
OPSMENDER_SMTP_PORT=587
OPSMENDER_SMTP_USER=opsmender
OPSMENDER_SMTP_PASSWORD=...
OPSMENDER_SMTP_FROM=opsmender@example.com
OPSMENDER_SMTP_USE_TLS=true
```

Why they matter:

- **`OPSMENDER_PUBLIC_BASE_URL`** controls the absolute URLs placed into invite and password-reset emails.
- **SMTP settings** control whether OpsMender can deliver those links automatically.

If the public base URL is wrong, the copied link and emailed link will point to the wrong host. Fix that first before debugging email delivery.

---

## 10. Recommended operating pattern

For most teams, the clean pattern is:

1. Keep one local admin account as break glass.
2. Configure OIDC or SAML per org for normal user login.
3. Use the People page to manage roles, activation state, and offboarding.
4. Use invites for local users only when there is a real reason not to use SSO.

That keeps day-to-day identity in your IdP while preserving an OpsMender-native recovery path.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Invite created, but recipient never got an email | SMTP not configured or SMTP delivery failed | Copy the one-time URL from the modal and send it manually. Then verify `OPSMENDER_SMTP_*`. |
| Invite link opens but cannot be consumed | Token expired, was revoked, or was already used | Use the resend action for a still-pending invite, or create a new invite if the old one is no longer pending. |
| Password reset email failed | Same SMTP path as invites | Use the returned reset URL directly and then fix SMTP. |
| Delete button stays disabled | User is still active or still appears on one or more rosters | Deactivate the user first, then remove them from all roster memberships. |
| User should be SSO-backed, but shows Local | They were created locally and have not yet come through a successful OIDC/SAML login | Have them sign in through the IdP once; OpsMender updates the recorded auth source on success. |
| OIDC/SAML badge opens the wrong place | The Settings route failed to load or the provider was removed | Open **Settings -> Workspace** directly and verify the provider row. |

---

## 12. Related guides

- [Auth Guide](auth-guide.md) — the default auth model: single workspace, email + admin invite, three roles.
- [Advanced Auth Guide](advanced-auth-guide.md) — optional OIDC + SAML + custom-domain login behavior.
- [Administrator Guide](admin-guide.md) — runtime config, MCP, integrations, models.
- [Getting Started](getting-started.md) — first local boot and first login.
- [Notification Preferences](notification-preferences.md) — channel routing for a specific operator.
