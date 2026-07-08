"""Per-tenant SSO login flow (OIDC).

Two routes:

* ``GET /auth/sso/{slug}/login`` — initiates the OIDC authorize redirect.
  The state parameter is a short-lived signed JWT that carries the org_id
  and a nonce so we don't need server-side state.
* ``GET /auth/sso/{slug}/callback`` — exchanges the code, verifies the
  id_token, JIT-provisions the user, and redirects to the dashboard with
  the OpsMender JWT in the URL fragment.

Logout is handled the existing way (frontend clears the token); IdP-side
single-logout (SLO) is intentionally out of scope for v1.
"""

from __future__ import annotations

import secrets as _secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import _auth_config, create_access_token, hash_password
from backend.api.deps import get_db
from backend.auth.oidc import OIDCClientConfig, OIDCError, build_authorize_url, exchange_code
from backend.auth.secrets import decrypt_secret
from backend.db.models import Organization
from backend.db.repos import OrganizationRepo, OrgSSOConfigRepo, UserRepo

router = APIRouter(prefix="/auth/sso", tags=["sso"])


_STATE_TTL_SECONDS = 300  # 5 minutes


def _build_redirect_uri(request: Request, slug: str) -> str:
    """Reconstruct the callback URL the IdP must echo back to.

    Uses ``X-Forwarded-Proto``/``X-Forwarded-Host`` if present so reverse
    proxies (which is the typical Domain-Isolation deployment) work.
    """
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}/auth/sso/{slug}/callback"


def _encode_state(org_id: uuid.UUID, nonce: str) -> str:
    settings = _auth_config()
    now = datetime.now(timezone.utc)
    payload = {
        "org_id": str(org_id),
        "nonce": nonce,
        "iat": now,
        "exp": now + timedelta(seconds=_STATE_TTL_SECONDS),
        "purpose": "sso_state",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_state(state: str) -> dict[str, Any]:
    settings = _auth_config()
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}")
    if payload.get("purpose") != "sso_state":
        raise HTTPException(status_code=400, detail="State not for SSO")
    return payload


async def _resolve_active_sso(
    db: AsyncSession, slug: str
) -> tuple[Organization, OIDCClientConfig, Any]:
    org = await OrganizationRepo.get_by_slug(db, slug)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    sso = await OrgSSOConfigRepo.get_for_org(db, org.id)
    if sso is None or not sso.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO is not enabled for this organization.",
        )
    try:
        plaintext_secret = decrypt_secret(sso.client_secret_encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    config = OIDCClientConfig(
        discovery_url=sso.discovery_url,
        client_id=sso.client_id,
        client_secret=plaintext_secret,
        scopes=sso.scopes,
    )
    return org, config, sso


@router.get("/{slug}/login")
async def sso_login(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    org, config, _sso = await _resolve_active_sso(db, slug)
    nonce = _secrets.token_urlsafe(16)
    state = _encode_state(org.id, nonce)
    redirect_uri = _build_redirect_uri(request, slug)
    try:
        url = await build_authorize_url(
            config, state=state, redirect_uri=redirect_uri, nonce=nonce
        )
    except OIDCError as exc:
        raise HTTPException(status_code=502, detail=f"OIDC discovery failed: {exc}")
    return RedirectResponse(url, status_code=302)


@router.get("/{slug}/callback")
async def sso_callback(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    org, config, sso = await _resolve_active_sso(db, slug)

    state_payload = _decode_state(state)
    if state_payload.get("org_id") != str(org.id):
        raise HTTPException(status_code=400, detail="State org mismatch")
    nonce = state_payload.get("nonce")

    redirect_uri = _build_redirect_uri(request, slug)
    try:
        claims = await exchange_code(
            config, code=code, redirect_uri=redirect_uri, nonce=nonce
        )
    except OIDCError as exc:
        raise HTTPException(status_code=400, detail=f"SSO failed: {exc}")

    email = claims.get(sso.email_claim) or claims.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="IdP did not return an email claim.")
    email = str(email).strip().lower()

    if sso.allowed_email_domains:
        allowed = [d.strip().lower() for d in sso.allowed_email_domains.split(",") if d.strip()]
        if allowed and not any(email.endswith("@" + d) for d in allowed):
            raise HTTPException(
                status_code=403,
                detail="Email domain not allowed for this organization.",
            )

    claims.get(sso.name_claim) or claims.get("name") or email.split("@")[0]

    # JIT-provision: find existing user by email, otherwise create one.
    auth_source = f"oidc:{org.slug}"
    user = await UserRepo.get_by_email(db, email)
    if user is None:
        # Username defaults to local-part of email (uniquified if needed).
        base_username = email.split("@")[0]
        username = base_username
        suffix = 1
        while await UserRepo.get_by_username(db, username):
            suffix += 1
            username = f"{base_username}{suffix}"
        # Random password — local password login is disabled for SSO users.
        random_pw = _secrets.token_urlsafe(32)
        user = await UserRepo.create(
            db,
            username=username,
            email=email,
            password_hash=hash_password(random_pw),
            auth_source=auth_source,
            role=sso.default_role,
            primary_org_id=org.id,
        )
    elif user.auth_source != auth_source:
        user = await UserRepo.update_fields(db, user.id, auth_source=auth_source) or user

    # Ensure user is linked to this org.
    if not await UserRepo.is_member(db, user.id, org.id):
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=org.id, role=sso.default_role
        )
    if user.primary_org_id is None:
        await UserRepo.set_primary_org(db, user.id, org.id)

    await db.commit()

    opsmender_token = create_access_token(user.id, user.role)

    # Hand the token back to the SPA via a fragment so it never hits server logs.
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    target = f"{scheme}://{host}/login#sso_token={quote(opsmender_token)}"
    return RedirectResponse(target, status_code=302)
