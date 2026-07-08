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
curl -H "Authorization: Bearer omk_..." https://<host>/api/v1/incidents
```

The token's role is the effective role for the request. For example, an
Operator token can use operator-accessible read and action endpoints but cannot
use administrator-only endpoints.

API tokens are not accepted for sign-in, self-service profile routes,
multi-factor enrollment, or live WebSocket streams.

## Revoke

Open **Settings** → **API Tokens**, then choose the revoke action for the token.
Revocation is immediate: the bearer credential returns `401` on its next use.

## Audit Trail

Creating or revoking a token writes an audit entry. When a token performs a
mutating token-management action, the actor is recorded as `api-token:<name>` so
the audit trail does not imply a human clicked the button.
