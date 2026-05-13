"""Slack / Discord OAuth helpers for bot-connector "Connect to …" flows.

Sprint 31 Steps 5–6. Shape mirrors the SAML SP keypair model: client
credentials live in env (``OPSMENDER_SLACK_OAUTH_CLIENT_ID`` /
``OPSMENDER_SLACK_OAUTH_CLIENT_SECRET`` / Discord equivalents), never in the
DB. The OAuth start route signs a short-lived JWT carrying the
connector_id so callbacks can't be cross-pollinated between connectors;
the callback verifies the JWT, exchanges the auth code with the
provider, and writes the resulting tokens into the connector's
``credentials`` JSON.

Slack OAuth response (v2):
    https://api.slack.com/methods/oauth.v2.access
    Returns ``access_token`` (bot token, "xoxb-…") plus ``team`` /
    ``bot_user_id``. Note: signing_secret is **not** returned — it is a
    per-app constant the operator must still paste manually before
    Slack webhook verification works.

Discord OAuth response (with ``bot`` scope):
    https://discord.com/developers/docs/topics/oauth2#bot-authorization-flow
    Returns ``access_token`` plus ``bot`` token (``access_token``) and
    ``guild`` info. Discord's public_key for interaction signature
    verification is **not** returned — same caveat as Slack.

Both providers therefore populate the bot token (so the connector can
send messages) but the operator must still configure the webhook
verification secret manually. The form's typed field for that secret is
still rendered after OAuth completes; the connector is left disabled
until the operator finishes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from backend.config_loader import AppConfig, BotOAuthConfig


STATE_TTL_SECONDS = 300  # 5 minutes
STATE_AUDIENCE = "opsmender-bot-oauth"

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_DEFAULT_SCOPES = "chat:write,commands,channels:read"

DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_DEFAULT_SCOPES = "bot applications.commands"
DISCORD_BOT_PERMISSIONS = "2048"  # Send Messages


@dataclass(frozen=True)
class OAuthResult:
    """Outcome of a successful provider code-exchange."""

    credentials: dict[str, str]
    """Subset of credential fields to merge into the connector."""

    detail: str
    """Short human-readable summary for the redirect query string."""


def _auth_config() -> AppConfig:
    return AppConfig.load()


def _platform_creds(
    cfg: BotOAuthConfig, platform: str
) -> tuple[str, str]:
    """Return ``(client_id, client_secret)`` or raise ``ValueError``."""
    if platform == "slack":
        if not cfg.slack_client_id or not cfg.slack_client_secret:
            raise ValueError("Slack OAuth is not configured on this server.")
        return cfg.slack_client_id, cfg.slack_client_secret
    if platform == "discord":
        if not cfg.discord_client_id or not cfg.discord_client_secret:
            raise ValueError("Discord OAuth is not configured on this server.")
        return cfg.discord_client_id, cfg.discord_client_secret
    raise ValueError(f"OAuth is not supported for platform '{platform}'.")


# ---------------------------------------------------------------------------
# State JWT
# ---------------------------------------------------------------------------


def sign_state(
    *,
    connector_id: str,
    platform: str,
    org_id: str,
    user_id: str,
) -> str:
    """Sign a short-lived state JWT carrying the connector_id.

    The provider echoes this string back on the callback; we verify the
    signature and TTL before trusting any code-exchange result.
    """
    cfg = _auth_config()
    now = int(time.time())
    payload = {
        "iss": "opsmender",
        "aud": STATE_AUDIENCE,
        "sub": connector_id,
        "plat": platform,
        "org": org_id,
        "uid": user_id,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(
        payload,
        cfg.auth.jwt_secret,
        algorithm=cfg.auth.jwt_algorithm,
    )


def verify_state(token: str) -> dict[str, Any]:
    """Decode + validate a state JWT. Raises ``ValueError`` on failure."""
    cfg = _auth_config()
    try:
        return jwt.decode(
            token,
            cfg.auth.jwt_secret,
            algorithms=[cfg.auth.jwt_algorithm],
            audience=STATE_AUDIENCE,
        )
    except JWTError as exc:
        raise ValueError(f"Invalid OAuth state: {exc}") from exc


# ---------------------------------------------------------------------------
# Authorization URL builders
# ---------------------------------------------------------------------------


def build_authorize_url(
    *,
    platform: str,
    state: str,
    redirect_uri: str,
    cfg: BotOAuthConfig | None = None,
) -> str:
    cfg = cfg or _auth_config().bot_oauth
    client_id, _ = _platform_creds(cfg, platform)

    if platform == "slack":
        params = {
            "client_id": client_id,
            "scope": SLACK_DEFAULT_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"

    if platform == "discord":
        params = {
            "client_id": client_id,
            "scope": DISCORD_DEFAULT_SCOPES,
            "permissions": DISCORD_BOT_PERMISSIONS,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"

    raise ValueError(f"Unsupported OAuth platform: {platform}")


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


async def exchange_code(
    *,
    platform: str,
    code: str,
    redirect_uri: str,
    client: httpx.AsyncClient | None = None,
    cfg: BotOAuthConfig | None = None,
) -> OAuthResult:
    """Exchange an OAuth ``code`` with the provider's token endpoint."""
    cfg = cfg or _auth_config().bot_oauth
    client_id, client_secret = _platform_creds(cfg, platform)

    async def _post(
        url: str,
        data: Mapping[str, str],
        *,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        owner = client is None
        c = client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await c.post(url, data=dict(data), auth=auth)
        finally:
            if owner:
                await c.aclose()
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError(f"{platform} token endpoint returned non-object body")
        return body

    if platform == "slack":
        body = await _post(
            SLACK_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if not body.get("ok", False):
            raise ValueError(
                f"Slack rejected the code exchange: {body.get('error', 'unknown')}"
            )
        bot_token = body.get("access_token")
        if not isinstance(bot_token, str) or not bot_token:
            raise ValueError("Slack response did not include access_token.")
        team = body.get("team") or {}
        team_name = team.get("name") if isinstance(team, dict) else None
        return OAuthResult(
            credentials={"bot_token": bot_token},
            detail=f"Connected to Slack workspace '{team_name}'"
            if team_name
            else "Connected to Slack workspace.",
        )

    if platform == "discord":
        body = await _post(
            DISCORD_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
        )
        bot_token = body.get("access_token")
        if not isinstance(bot_token, str) or not bot_token:
            raise ValueError("Discord response did not include access_token.")
        guild = body.get("guild") or {}
        guild_name = guild.get("name") if isinstance(guild, dict) else None
        return OAuthResult(
            credentials={"bot_token": bot_token},
            detail=f"Connected to Discord guild '{guild_name}'"
            if guild_name
            else "Connected to Discord guild.",
        )

    raise ValueError(f"Unsupported OAuth platform: {platform}")


# ---------------------------------------------------------------------------
# Helpers exposed for tests / route module
# ---------------------------------------------------------------------------


def is_platform_enabled(platform: str, *, cfg: BotOAuthConfig | None = None) -> bool:
    cfg = cfg or _auth_config().bot_oauth
    return cfg.is_enabled(platform)


SUPPORTED_PLATFORMS = ("slack", "discord")
