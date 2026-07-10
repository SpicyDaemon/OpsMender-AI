"""Single-workspace organization settings endpoints."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    OrgEmailSettingsResponse,
    OrgEmailSettingsUpsert,
    EmailSettingsTestRequest,
    EmailSettingsTestResponse,
    OrganizationDomainCreate,
    OrganizationDomainListResponse,
    OrganizationDomainResponse,
    OrganizationResponse,
    OrganizationUpdate,
    OrgSAMLConfigCreate,
    OrgSAMLConfigResponse,
    OrgSSOConfigCreate,
    OrgSSOConfigResponse,
    TenantContextResponse,
)
from backend.auth.secrets import encrypt_secret
from backend.db.repos import (
    OrganizationDomainRepo,
    OrgEmailSettingsRepo,
    OrganizationRepo,
    OrgSAMLConfigRepo,
    OrgSSOConfigRepo,
)
from backend.reports.email import build_email_channel, resolve_email_settings

router = APIRouter(prefix="/organizations", tags=["organizations"])

# Separate router for public tenant resolution (no auth, no /organizations prefix).
tenant_router = APIRouter(prefix="/tenant", tags=["tenant"])


@tenant_router.get("/resolve", response_model=TenantContextResponse)
async def resolve_tenant(
    db: AsyncSession = Depends(get_db),
    host: str | None = Header(default=None, alias="Host"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    """Public — return the single workspace's context (name, branding, SSO).

    OpsMender runs one organization per instance, so this always resolves the
    sole workspace. That lets the org name/branding render everywhere — the top
    bar after login *and* the login/register pages before authentication.

    A request whose host matches a registered custom domain is additionally
    flagged ``pinned`` (the canonical branded host); otherwise the same single
    workspace is returned unpinned. No host or no org yields an empty context.
    """
    raw_host = x_forwarded_host or host
    normalized = OrganizationDomainRepo.normalize(raw_host or "") or None

    org = None
    pinned = False
    if raw_host:
        match = await OrganizationDomainRepo.find_by_host(db, raw_host)
        if match is not None:
            org = await OrganizationRepo.get_by_id(db, match.org_id)
            pinned = org is not None

    if org is None:
        orgs = await OrganizationRepo.list_all(db)
        org = orgs[0] if orgs else None

    if org is None:
        return TenantContextResponse(pinned=False, host=normalized)

    sso = await OrgSSOConfigRepo.get_for_org(db, org.id)
    sso_enabled = sso is not None and sso.is_active
    saml = await OrgSAMLConfigRepo.get_for_org(db, org.id)
    saml_enabled = saml is not None and saml.is_active
    return TenantContextResponse(
        pinned=pinned,
        org_id=org.id,
        org_name=org.name,
        org_slug=org.slug,
        branding=org.branding,
        host=normalized,
        sso_enabled=sso_enabled,
        sso_login_path=f"/auth/sso/{org.slug}/login" if sso_enabled else None,
        saml_enabled=saml_enabled,
        saml_login_path=f"/auth/saml/{org.slug}/login" if saml_enabled else None,
    )


# Organization mutations and private settings require the global admin role.
admin_dependency = Depends(require_role("admin"))


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_org_id: uuid.UUID = Depends(get_current_org),
):
    """Return the authenticated member's workspace identity and branding."""
    if org_id != current_org_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.put(
    "/{org_id}", response_model=OrganizationResponse, dependencies=[admin_dependency]
)
async def update_organization(
    org_id: uuid.UUID, req: OrganizationUpdate, db: AsyncSession = Depends(get_db)
):
    """Update organization details."""
    org = await OrganizationRepo.update(
        db, org_id, name=req.name, slug=req.slug, branding=req.branding
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()
    return org


@router.get(
    "/{org_id}/notification-settings",
    response_model=NotificationSettingsResponse,
    dependencies=[admin_dependency],
)
async def get_notification_settings(
    org_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Get the org-wide notification settings (admin-only)."""
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return NotificationSettingsResponse(
        org_id=org.id,
        notification_dedup_window_minutes=org.notification_dedup_window_minutes,
        slack_incident_channels_enabled=getattr(
            org, "slack_incident_channels_enabled", False
        ),
    )


@router.put(
    "/{org_id}/notification-settings",
    response_model=NotificationSettingsResponse,
    dependencies=[admin_dependency],
)
async def update_notification_settings(
    org_id: uuid.UUID,
    body: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update the org-wide notification settings (admin-only)."""
    updated = await OrganizationRepo.update(
        db,
        org_id,
        notification_dedup_window_minutes=body.notification_dedup_window_minutes,
        slack_incident_channels_enabled=body.slack_incident_channels_enabled,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()
    return NotificationSettingsResponse(
        org_id=updated.id,
        notification_dedup_window_minutes=updated.notification_dedup_window_minutes,
        slack_incident_channels_enabled=getattr(
            updated, "slack_incident_channels_enabled", False
        ),
    )


@router.get(
    "/{org_id}/email-settings",
    response_model=OrgEmailSettingsResponse,
    dependencies=[admin_dependency],
)
async def get_email_settings(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if await OrganizationRepo.get_by_id(db, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    row = await OrgEmailSettingsRepo.get_for_org(db, org_id)
    settings = await resolve_email_settings(db, org_id)
    if settings is None:
        return OrgEmailSettingsResponse(
            org_id=org_id,
            configured=False,
            host="",
            port=587,
            security="starttls",
            username=None,
            from_name="OpsMender",
            from_address="",
            has_password=False,
            source=None,
        )
    return OrgEmailSettingsResponse(
        org_id=org_id,
        configured=True,
        host=settings.host,
        port=settings.port,
        security=settings.security,
        username=settings.username,
        from_name=settings.from_name,
        from_address=settings.from_address,
        has_password=bool(settings.password),
        source="database" if row is not None else "environment",
    )


@router.put(
    "/{org_id}/email-settings",
    response_model=OrgEmailSettingsResponse,
    dependencies=[admin_dependency],
)
async def upsert_email_settings(
    org_id: uuid.UUID,
    body: OrgEmailSettingsUpsert,
    db: AsyncSession = Depends(get_db),
):
    if await OrganizationRepo.get_by_id(db, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    existing = await OrgEmailSettingsRepo.get_for_org(db, org_id)
    if body.clear_password:
        encrypted = ""
    elif body.password is not None:
        encrypted = encrypt_secret(body.password)
    elif existing is not None:
        encrypted = None
    else:
        encrypted = ""
    row = await OrgEmailSettingsRepo.upsert(
        db,
        org_id,
        host=body.host,
        port=body.port,
        security=body.security,
        username=body.username,
        password_encrypted=encrypted,
        from_name=body.from_name,
        from_address=body.from_address,
    )
    await db.commit()
    return OrgEmailSettingsResponse(
        org_id=org_id,
        host=row.host,
        port=row.port,
        security=row.security,
        username=row.username,
        from_name=row.from_name,
        from_address=row.from_address,
        has_password=bool(row.password_encrypted),
        source="database",
    )


@router.delete(
    "/{org_id}/email-settings",
    status_code=204,
    dependencies=[admin_dependency],
)
async def delete_email_settings(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if not await OrgEmailSettingsRepo.delete(db, org_id):
        raise HTTPException(status_code=404, detail="SMTP settings not found")
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{org_id}/email-settings/test",
    response_model=EmailSettingsTestResponse,
    dependencies=[admin_dependency],
)
async def test_email_settings(
    org_id: uuid.UUID,
    body: EmailSettingsTestRequest,
    db: AsyncSession = Depends(get_db),
):
    settings = await resolve_email_settings(db, org_id)
    if settings is None:
        return EmailSettingsTestResponse(success=False, detail="SMTP not configured")
    attempt = await build_email_channel(settings).send(
        recipient=body.recipient,
        subject="OpsMender SMTP test",
        body="Your OpsMender SMTP configuration is working.",
    )
    return EmailSettingsTestResponse(
        success=attempt.status == "sent",
        detail="Test email sent."
        if attempt.status == "sent"
        else (attempt.error or "Send failed"),
    )


@router.get(
    "/{org_id}/domains",
    response_model=OrganizationDomainListResponse,
    dependencies=[admin_dependency],
)
async def list_organization_domains(
    org_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    items = await OrganizationDomainRepo.list_for_org(db, org_id)
    return {"items": items, "total": len(items)}


@router.post(
    "/{org_id}/domains",
    response_model=OrganizationDomainResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[admin_dependency],
)
async def create_organization_domain(
    org_id: uuid.UUID,
    req: OrganizationDomainCreate,
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    normalized = OrganizationDomainRepo.normalize(req.domain)
    if not normalized or "." not in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain must be a valid hostname (e.g. acme.opsmender.example.com).",
        )

    existing = await OrganizationDomainRepo.find_by_host(db, normalized)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Domain '{normalized}' is already registered.",
        )

    try:
        row = await OrganizationDomainRepo.create(
            db,
            org_id=org_id,
            domain=normalized,
            is_primary=req.is_primary,
            verified=req.verified,
        )
        if req.is_primary:
            await OrganizationDomainRepo.set_primary(
                db, org_id=org_id, domain_id=row.id
            )
            row = await OrganizationDomainRepo.get_by_id(db, row.id)  # type: ignore[assignment]
        await db.commit()
        return row
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create domain: {e}",
        )


@router.post(
    "/{org_id}/domains/{domain_id}/set-primary",
    response_model=OrganizationDomainResponse,
    dependencies=[admin_dependency],
)
async def set_primary_domain(
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    row = await OrganizationDomainRepo.set_primary(
        db, org_id=org_id, domain_id=domain_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Domain not found for this org")
    await db.commit()
    return row


@router.delete(
    "/{org_id}/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[admin_dependency],
)
async def delete_organization_domain(
    org_id: uuid.UUID, domain_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    row = await OrganizationDomainRepo.get_by_id(db, domain_id)
    if row is None or row.org_id != org_id:
        raise HTTPException(status_code=404, detail="Domain not found for this org")
    await OrganizationDomainRepo.delete(db, domain_id)
    await db.commit()


# ---------------------------------------------------------------------------
# Per-org SSO configuration (admin only)
# ---------------------------------------------------------------------------


def _sso_to_response(row) -> dict:
    """Build a response dict that never leaks the encrypted secret."""
    return {
        "id": row.id,
        "org_id": row.org_id,
        "provider": row.provider,
        "is_active": row.is_active,
        "discovery_url": row.discovery_url,
        "client_id": row.client_id,
        "has_client_secret": bool(row.client_secret_encrypted),
        "scopes": row.scopes,
        "email_claim": row.email_claim,
        "name_claim": row.name_claim,
        "default_role": row.default_role,
        "allowed_email_domains": row.allowed_email_domains,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get(
    "/{org_id}/sso",
    response_model=OrgSSOConfigResponse,
    dependencies=[admin_dependency],
)
async def get_organization_sso(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    if await OrganizationRepo.get_by_id(db, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    row = await OrgSSOConfigRepo.get_for_org(db, org_id)
    if row is None:
        return OrgSSOConfigResponse(
            configured=False,
            org_id=org_id,
            provider="oidc",
            is_active=False,
            discovery_url="",
            client_id="",
            has_client_secret=False,
            scopes="openid email profile",
            email_claim="email",
            name_claim="name",
            default_role="operator",
            allowed_email_domains=None,
        )
    return _sso_to_response(row)


@router.put(
    "/{org_id}/sso",
    response_model=OrgSSOConfigResponse,
    dependencies=[admin_dependency],
)
async def upsert_organization_sso(
    org_id: uuid.UUID,
    req: OrgSSOConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    existing = await OrgSSOConfigRepo.get_for_org(db, org_id)
    # On update, allow callers to omit the secret to keep the existing one.
    if req.client_secret:
        encrypted = encrypt_secret(req.client_secret)
    elif existing is not None:
        encrypted = ""  # signals "keep existing" to the repo
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_secret is required when creating a new SSO config.",
        )

    row = await OrgSSOConfigRepo.upsert(
        db,
        org_id=org_id,
        provider=req.provider,
        discovery_url=req.discovery_url,
        client_id=req.client_id,
        client_secret_encrypted=encrypted,
        is_active=req.is_active,
        scopes=req.scopes,
        email_claim=req.email_claim,
        name_claim=req.name_claim,
        default_role=req.default_role,
        allowed_email_domains=req.allowed_email_domains,
    )
    await db.commit()
    return _sso_to_response(row)


@router.delete(
    "/{org_id}/sso",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[admin_dependency],
)
async def delete_organization_sso(
    org_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    deleted = await OrgSSOConfigRepo.delete(db, org_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="No SSO config for this organization"
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Per-org SAML configuration (admin only) — Sprint 30
# ---------------------------------------------------------------------------


def _saml_to_response(row) -> dict:
    """Build a SAML response dict. The IdP metadata XML is intentionally not
    returned in full — only a flag indicating it exists — to keep the admin
    UI compact (the XML is often hundreds of lines)."""
    return {
        "id": row.id,
        "org_id": row.org_id,
        "is_active": row.is_active,
        "idp_metadata_url": row.idp_metadata_url,
        "has_idp_metadata_xml": bool(row.idp_metadata_xml),
        "email_attribute": row.email_attribute,
        "name_attribute": row.name_attribute,
        "default_role": row.default_role,
        "allowed_email_domains": row.allowed_email_domains,
        "want_assertions_signed": row.want_assertions_signed,
        "want_response_signed": row.want_response_signed,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get(
    "/{org_id}/saml",
    response_model=OrgSAMLConfigResponse,
    dependencies=[admin_dependency],
)
async def get_organization_saml(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    if await OrganizationRepo.get_by_id(db, org_id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    row = await OrgSAMLConfigRepo.get_for_org(db, org_id)
    if row is None:
        return OrgSAMLConfigResponse(
            configured=False,
            org_id=org_id,
            is_active=False,
            idp_metadata_url=None,
            has_idp_metadata_xml=False,
            email_attribute="email",
            name_attribute="name",
            default_role="operator",
            allowed_email_domains=None,
            want_assertions_signed=True,
            want_response_signed=True,
        )
    return _saml_to_response(row)


@router.put(
    "/{org_id}/saml",
    response_model=OrgSAMLConfigResponse,
    dependencies=[admin_dependency],
)
async def upsert_organization_saml(
    org_id: uuid.UUID,
    req: OrgSAMLConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if bool(req.idp_metadata_url) == bool(req.idp_metadata_xml):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Provide exactly one of idp_metadata_url or idp_metadata_xml."),
        )

    row = await OrgSAMLConfigRepo.upsert(
        db,
        org_id=org_id,
        idp_metadata_url=req.idp_metadata_url,
        idp_metadata_xml=req.idp_metadata_xml,
        is_active=req.is_active,
        email_attribute=req.email_attribute,
        name_attribute=req.name_attribute,
        default_role=req.default_role,
        allowed_email_domains=req.allowed_email_domains,
        want_assertions_signed=req.want_assertions_signed,
        want_response_signed=req.want_response_signed,
    )
    await db.commit()
    return _saml_to_response(row)


@router.delete(
    "/{org_id}/saml",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[admin_dependency],
)
async def delete_organization_saml(
    org_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    deleted = await OrgSAMLConfigRepo.delete(db, org_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="No SAML config for this organization"
        )
    await db.commit()
