"""Organization management endpoints (Phase 4).

Only global admins (User.role == 'admin') can manage organizations.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    OrganizationCreate,
    OrganizationDomainCreate,
    OrganizationDomainListResponse,
    OrganizationDomainResponse,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUpdate,
    OrganizationUserListResponse,
    TenantContextResponse,
    UserOrganizationLink,
)
from backend.db.models import User
from backend.db.repos import OrganizationDomainRepo, OrganizationRepo, UserRepo

router = APIRouter(prefix="/organizations", tags=["organizations"])

# Separate router for public tenant resolution (no auth, no /organizations prefix).
tenant_router = APIRouter(prefix="/tenant", tags=["tenant"])


@tenant_router.get("/resolve", response_model=TenantContextResponse)
async def resolve_tenant(
    db: AsyncSession = Depends(get_db),
    host: str | None = Header(default=None, alias="Host"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
):
    """Public — return the tenant pinned to the request hostname, if any.

    Used by the frontend to:
    - Show org branding on login/register before the user authenticates.
    - Hide the org switcher when the host pins a tenant (no ambiguity).
    """
    raw_host = x_forwarded_host or host
    normalized = OrganizationDomainRepo.normalize(raw_host or "")
    if not raw_host:
        return TenantContextResponse(pinned=False)

    match = await OrganizationDomainRepo.find_by_host(db, raw_host)
    if match is None:
        return TenantContextResponse(pinned=False, host=normalized)

    org = await OrganizationRepo.get_by_id(db, match.org_id)
    if org is None:
        return TenantContextResponse(pinned=False, host=normalized)

    return TenantContextResponse(
        pinned=True,
        org_id=org.id,
        org_name=org.name,
        org_slug=org.slug,
        branding=org.branding,
        host=normalized,
    )

# All routes in this module require the global 'admin' role.
# (This is separate from the per-organization role).
admin_dependency = Depends(require_role("admin"))


@router.get("", response_model=OrganizationListResponse, dependencies=[admin_dependency])
async def list_organizations(db: AsyncSession = Depends(get_db)):
    """List all organizations in the system."""
    items = await OrganizationRepo.list_all(db)
    return {"items": items, "total": len(items)}


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[admin_dependency],
)
async def create_organization(
    req: OrganizationCreate, db: AsyncSession = Depends(get_db)
):
    """Create a new organization."""
    # Check for slug collision
    if req.slug:
        # Note: OrganizationRepo.create handles default slug if None
        pass

    try:
        org = await OrganizationRepo.create(db, name=req.name, slug=req.slug, branding=req.branding)
        await db.commit()
        return org
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create organization: {str(e)}",
        )


@router.get(
    "/{org_id}", response_model=OrganizationResponse, dependencies=[admin_dependency]
)
async def get_organization(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get organization details."""
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
    org = await OrganizationRepo.update(db, org_id, name=req.name, slug=req.slug, branding=req.branding)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()
    return org


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[admin_dependency])
async def delete_organization(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete an organization."""
    success = await OrganizationRepo.delete(db, org_id)
    if not success:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()


@router.get(
    "/{org_id}/users",
    response_model=OrganizationUserListResponse,
    dependencies=[admin_dependency],
)
async def list_organization_users(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List all users belonging to an organization."""
    items = await UserRepo.list_by_org(db, org_id)
    return {"items": items, "total": len(items)}


@router.post(
    "/{org_id}/users", status_code=status.HTTP_204_NO_CONTENT, dependencies=[admin_dependency]
)
async def add_user_to_organization(
    org_id: uuid.UUID, req: UserOrganizationLink, db: AsyncSession = Depends(get_db)
):
    """Link a user to an organization."""
    # Verify user exists
    user = await UserRepo.get_by_id(db, req.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify org exists
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        await UserRepo.add_to_organization(db, user_id=req.user_id, org_id=org_id, role=req.role)
        
        # If user has no primary org, set this one
        if user.primary_org_id is None:
            await UserRepo.set_primary_org(db, user.id, org_id)
            
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to add user to organization: {str(e)}",
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
            detail="Domain must be a valid hostname (e.g. acme.aim.example.com).",
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


@router.delete(
    "/{org_id}/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[admin_dependency],
)
async def remove_user_from_organization(
    org_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Remove a user from an organization."""
    success = await UserRepo.remove_from_organization(db, user_id=user_id, org_id=org_id)
    if not success:
        raise HTTPException(status_code=404, detail="User-Organization link not found")
    
    # If this was the user's primary org, clear it
    user = await UserRepo.get_by_id(db, user_id)
    if user and user.primary_org_id == org_id:
        # Set to None or pick another one? For now, None.
        await UserRepo.set_primary_org(db, user_id, None) # type: ignore
        
    await db.commit()
