# API Tokens

API tokens let scripts and automation call the REST API with a named bearer
credential. Tokens are scoped to one role, can be revoked anytime, and show the
secret exactly once when created.

## Create

1. Open **Settings**.
2. In **API Tokens**, choose **Create token**.
3. Enter a name and role.
4. Copy the `omk_...` token from the one-time confirmation.

The stored record keeps only a sha256 hash and a short prefix for display. The
full secret is not available after the create modal closes.

## Use

Send the token in the `Authorization` header:

```bash
curl -H "Authorization: Bearer omk_..." https://<host>/incidents
```

A token acts as the person who created it, with the lower of two roles: the
token's own role and that person's current role. An Operator token can use
operator-accessible read and action endpoints but never administrator-only
ones, even when an Admin created it. Team-scoped actions, such as reassigning
an incident or force-taking it, check the creator's team memberships. If the
creator is demoted, their tokens drop to the new role; if the creator is
deactivated or deleted, their tokens stop working.

API tokens are not accepted for sign-in, self-service profile routes,
multi-factor enrollment, or live WebSocket streams.

## Revoke

Open **Settings** → **API Tokens**, then choose the revoke action for the token.
Revocation is immediate: the bearer credential returns `401` on its next use.

A password change revokes every token the person created, at that moment:
when they change their own password, when an admin sets them a temporary
password, and when they set a new one from a reset link. The tokens show as
revoked in the list, each with an Activity entry naming the reason, and return
`401` from then on. Create new tokens afterwards. An admin who changes their
own password revokes the tokens they minted, including ones used by
automation. Tokens don't expire on their own.

## Audit Trail

Creating or revoking a token writes an audit entry. When a token performs a
mutating token-management action, the actor is recorded as `api-token:<name>` so
the audit trail does not imply a human clicked the button.
