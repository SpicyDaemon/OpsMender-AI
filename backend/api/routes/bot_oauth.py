"""Slack / Discord OAuth "Connect to …" flow for bot connectors.

Sprint 31 Steps 5–6. Two admin-only routes per supported platform:

* ``GET /bot-connectors/oauth/{platform}/start?connector_id=…`` — 302
  redirect to the provider's authorize URL with a signed-JWT ``state``
  carrying the connector_id (5-minute TTL).
* ``GET /bot-connectors/oauth/{platform}/callback?code=…&state=…`` —
  verifies the state, exchanges the code for tokens, merges the bot
  token into the connector's ``credentials`` JSON, and redirects back
  to ``/dashboard/paging/notification-channels``.

Client credentials (``OPSMENDER_SLACK_OAUTH_CLIENT_ID`` etc) live in env, not
in the DB. When unset, the routes return 503 and the UI falls back to
manual paste.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse  # noqa: F401 — used by callback
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.auth.bot_oauth import (
    SUPPORTED_PLATFORMS,
    build_authorize_url,
    exchange_code,
    is_platform_enabled,
    sign_state,
    verify_state,
)
from backend.config_loader import Config
from backend.db.models import User
from backend.db.repos import BotConnectorRepo

router = APIRouter(prefix="/bot-connectors/oauth", tags=["bot-connectors"])


def _public_base_url(request: Request) -> str:
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _redirect_uri(request: Request, platform: str) -> str:
    return f"{_public_base_url(request)}/bot-connectors/oauth/{platform}/callback"


def _ensure_supported(platform: str) -> None:
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth is not supported for platform '{platform}'.",
        )


def _ensure_enabled(platform: str) -> None:
    if not is_platform_enabled(platform):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{platform.title()} OAuth client credentials are not configured. "
                f"Set OPSMENDER_{platform.upper()}_OAUTH_CLIENT_ID and "
                f"OPSMENDER_{platform.upper()}_OAUTH_CLIENT_SECRET in the environment."
            ),
        )


@router.get(
    "/{platform}/start",
    summary="Begin a Slack/Discord OAuth install for a bot connector",
)
async def start_oauth(
    platform: str,
    request: Request,
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Returns ``{"authorize_url": "..."}`` for the frontend to navigate to.

    A direct ``302`` won't work for the browser-side flow because the
    initiating page authenticates with a Bearer token from localStorage,
    which the browser cannot attach to a top-level navigation. The
    frontend therefore ``fetch()``-es this endpoint with auth and then
    sets ``window.location.href``.
    """
    _ensure_supported(platform)
    _ensure_enabled(platform)

    connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )
    if connector.platform != platform:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Connector platform is '{connector.platform}', not '{platform}'."
            ),
        )

    state = sign_state(
        connector_id=str(connector_id),
        platform=platform,
        org_id=str(org_id),
        user_id=str(user.id),
    )
    url = build_authorize_url(
        platform=platform,
        state=state,
        redirect_uri=_redirect_uri(request, platform),
    )
    return {"authorize_url": url}


@router.get(
    "/{platform}/callback",
    summary="OAuth callback — exchanges code for tokens and stores them",
)
async def oauth_callback(
    platform: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public route. Authentication is enforced by validating the signed
    state JWT we issued at /start. The provider redirects the
    operator's browser here without our session cookie, so we can't
    require the usual JWT auth dep here.
    """
    _ensure_supported(platform)
    _ensure_enabled(platform)

    code = request.query_params.get("code")
    state_raw = request.query_params.get("state")
    error = request.query_params.get("error")

    base = _public_base_url(request)

    def _redirect_with(params: dict[str, str]) -> RedirectResponse:
        target = f"{base}/dashboard/paging/notification-channels?{urlencode(params)}"
        return RedirectResponse(target, status_code=status.HTTP_302_FOUND)

    if error:
        return _redirect_with({"bot_oauth": "error", "detail": error})

    if not code or not state_raw:
        return _redirect_with(
            {"bot_oauth": "error", "detail": "Missing OAuth code or state."}
        )

    try:
        state = verify_state(state_raw)
    except ValueError as exc:
        return _redirect_with({"bot_oauth": "error", "detail": str(exc)})

    if state.get("plat") != platform:
        return _redirect_with(
            {"bot_oauth": "error", "detail": "Platform mismatch in OAuth state."}
        )

    try:
        connector_id = uuid.UUID(str(state["sub"]))
        org_id = uuid.UUID(str(state["org"]))
    except (KeyError, ValueError):
        return _redirect_with(
            {"bot_oauth": "error", "detail": "Malformed OAuth state."}
        )

    connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if connector is None or connector.platform != platform:
        return _redirect_with(
            {"bot_oauth": "error", "detail": "Connector no longer exists."}
        )

    redirect_uri = _redirect_uri(request, platform)
    try:
        result = await exchange_code(
            platform=platform,
            code=code,
            redirect_uri=redirect_uri,
        )
    except Exception as exc:  # noqa: BLE001 — provider errors vary widely
        return _redirect_with(
            {"bot_oauth": "error", "detail": f"Code exchange failed: {exc}"}
        )

    merged = dict(connector.credentials or {})
    merged.update(result.credentials)
    connector.credentials = merged
    connector.status = "configured"
    await db.flush()
    await db.commit()

    return _redirect_with(
        {
            "bot_oauth": "ok",
            "platform": platform,
            "connector_id": str(connector_id),
            "detail": result.detail,
        }
    )
