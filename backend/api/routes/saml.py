"""Per-tenant SAML SSO flow (Sprint 30).

Three routes mirror the OIDC flow under ``/auth/saml/{slug}``:

* ``GET  /auth/saml/{slug}/login``    — SP-initiated AuthnRequest redirect.
* ``POST /auth/saml/{slug}/acs``      — Assertion Consumer Service.
* ``GET  /auth/saml/{slug}/metadata`` — SP metadata XML (for IdP admins).

JIT user provisioning + allowed-email-domain enforcement reuses the same
shape as the OIDC flow in :mod:`backend.api.routes.sso`.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import create_access_token, hash_password
from backend.api.deps import get_db
from backend.auth.saml import (
    SAMLError,
    SAMLOrgConfig,
    SPKeypair,
    _RequestData,
    build_authn_request,
    build_settings,
    fetch_idp_metadata,
    first_attribute,
    process_acs,
    render_sp_metadata,
)
from backend.config_loader import Config
from backend.db.models import Organization
from backend.db.repos import OrganizationRepo, OrgSAMLConfigRepo, UserRepo

router = APIRouter(prefix="/auth/saml", tags=["saml"])


def _public_base_url(request: Request) -> str:
    """Reconstruct the SP's externally-visible scheme://host base URL.

    Honors ``X-Forwarded-Proto``/``X-Forwarded-Host`` so reverse-proxied
    deployments (the typical Domain-Isolation setup) work.
    """
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _sp_keypair_or_503() -> SPKeypair:
    cfg = Config.load()
    saml_cfg = cfg.saml
    if not saml_cfg.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "SAML SP keypair is not configured. Set OPSMENDER_SAML_SP_CERT and "
                "OPSMENDER_SAML_SP_KEY in the environment."
            ),
        )
    return SPKeypair(
        cert=saml_cfg.sp_cert or "",
        key=saml_cfg.sp_key or "",
        entity_id_override=saml_cfg.sp_entity_id,
    )


async def _resolve_active_saml(
    db: AsyncSession, slug: str
) -> tuple[Organization, SAMLOrgConfig, Any]:
    org = await OrganizationRepo.get_by_slug(db, slug)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    saml_row = await OrgSAMLConfigRepo.get_for_org(db, org.id)
    if saml_row is None or not saml_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML SSO is not enabled for this organization.",
        )
    org_cfg = SAMLOrgConfig(
        org_slug=org.slug,
        is_active=saml_row.is_active,
        idp_metadata_url=saml_row.idp_metadata_url,
        idp_metadata_xml=saml_row.idp_metadata_xml,
        email_attribute=saml_row.email_attribute,
        name_attribute=saml_row.name_attribute,
        want_assertions_signed=saml_row.want_assertions_signed,
        want_response_signed=saml_row.want_response_signed,
    )
    return org, org_cfg, saml_row


def _request_data(request: Request, post: dict[str, Any] | None = None) -> _RequestData:
    """Build a `_RequestData` for the SAML helper from the FastAPI request.

    OneLogin_Saml2_Auth doesn't read directly from FastAPI/Starlette; we
    feed it a normalised dict.
    """
    fwd_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    fwd_host = request.headers.get("x-forwarded-host") or (
        request.headers.get("host") or request.url.netloc
    )
    https = fwd_proto == "https"
    if ":" in fwd_host:
        host, port_str = fwd_host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 443 if https else 80
    else:
        port = 443 if https else 80
    return _RequestData(
        https=https,
        http_host=fwd_host,
        server_port=port,
        request_uri=request.url.path,
        get_data=dict(request.query_params),
        post_data=post or {},
    )


@router.get("/{slug}/metadata", response_class=Response)
async def saml_metadata(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Return the SP metadata XML for the IdP admin to upload."""
    sp = _sp_keypair_or_503()
    org, org_cfg, _row = await _resolve_active_saml(db, slug)
    try:
        idp = await fetch_idp_metadata(org_cfg)
        settings = build_settings(
            sp_keypair=sp,
            org=org_cfg,
            base_url=_public_base_url(request),
            idp=idp,
        )
        xml = render_sp_metadata(settings)
    except SAMLError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=xml, media_type="application/xml")


@router.get("/{slug}/login")
async def saml_login(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    """SP-initiated SSO — redirect the browser to the IdP."""
    sp = _sp_keypair_or_503()
    org, org_cfg, _row = await _resolve_active_saml(db, slug)
    try:
        idp = await fetch_idp_metadata(org_cfg)
        settings = build_settings(
            sp_keypair=sp,
            org=org_cfg,
            base_url=_public_base_url(request),
            idp=idp,
        )
    except SAMLError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    relay_state = _public_base_url(request) + f"/auth/saml/{slug}/acs"
    rd = _request_data(request)
    try:
        url = build_authn_request(
            settings=settings, request_data=rd, relay_state=relay_state
        )
    except Exception as exc:  # pragma: no cover - python3-saml internal failure
        raise HTTPException(status_code=502, detail=f"AuthnRequest build failed: {exc}")
    return RedirectResponse(url, status_code=302)


@router.post("/{slug}/acs")
async def saml_acs(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Validate the IdP's SAML response and JIT-provision the user."""
    sp = _sp_keypair_or_503()
    org, org_cfg, saml_row = await _resolve_active_saml(db, slug)

    form = await request.form()
    post_data = {k: v for k, v in form.multi_items()}

    try:
        idp = await fetch_idp_metadata(org_cfg)
        settings = build_settings(
            sp_keypair=sp,
            org=org_cfg,
            base_url=_public_base_url(request),
            idp=idp,
        )
    except SAMLError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    rd = _request_data(request, post=post_data)
    try:
        attributes, _name_id = process_acs(
            settings=settings, request_data=rd, expected_relay_state=None
        )
    except SAMLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    email = first_attribute(
        attributes,
        saml_row.email_attribute,
        fallback_keys=["email", "Email", "mail"],
    )
    if not email:
        raise HTTPException(
            status_code=400,
            detail="IdP response did not include an email attribute.",
        )
    email = email.strip().lower()

    if saml_row.allowed_email_domains:
        allowed = [
            d.strip().lower()
            for d in saml_row.allowed_email_domains.split(",")
            if d.strip()
        ]
        if allowed and not any(email.endswith("@" + d) for d in allowed):
            raise HTTPException(
                status_code=403,
                detail="Email domain not allowed for this organization.",
            )

    (
        first_attribute(
            attributes,
            saml_row.name_attribute,
            fallback_keys=["name", "displayName", "cn"],
        )
        or email.split("@")[0]
    )

    # JIT-provision (mirrors OIDC flow exactly).
    auth_source = f"saml:{org.slug}"
    user = await UserRepo.get_by_email(db, email)
    if user is None:
        base_username = email.split("@")[0]
        username = base_username
        suffix = 1
        while await UserRepo.get_by_username(db, username):
            suffix += 1
            username = f"{base_username}{suffix}"
        random_pw = _secrets.token_urlsafe(32)
        user = await UserRepo.create(
            db,
            username=username,
            email=email,
            password_hash=hash_password(random_pw),
            auth_source=auth_source,
            role=saml_row.default_role,
            primary_org_id=org.id,
        )
    elif user.auth_source != auth_source:
        user = await UserRepo.update_fields(db, user.id, auth_source=auth_source) or user

    if not await UserRepo.is_member(db, user.id, org.id):
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=org.id, role=saml_row.default_role
        )
    if user.primary_org_id is None:
        await UserRepo.set_primary_org(db, user.id, org.id)

    await db.commit()

    opsmender_token = create_access_token(user.id, user.role)
    target = f"{_public_base_url(request)}/login#sso_token={quote(opsmender_token)}"
    return RedirectResponse(target, status_code=302)
