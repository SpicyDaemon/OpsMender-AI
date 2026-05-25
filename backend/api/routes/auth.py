"""Auth routes — register and login.

POST /auth/register — create a new user
POST /auth/login    — authenticate and receive a JWT
GET  /auth/me       — return the current user profile
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from backend.api.deps import get_db
from backend.api.schemas import (
    LoginRequest,
    MyOrganizationListResponse,
    MyOrganizationResponse,
    RegisterRequest,
    TokenResponse,
    UserListResponse,
    UserResponse,
)
from backend.db.models import User
from backend.db.repos import OrganizationRepo, UserRepo

router = APIRouter(prefix="/auth", tags=["auth"])


def _is_development_mode() -> bool:
    """Sprint 56: self-registration is gated on deployment mode.

    Same env var the production safety guard uses
    (``backend/config_loader.py::check_production_safety``). Test
    fixtures + the ``scripts/dev_server.py`` launcher both set this to
    ``development`` so existing register-based bootstrap keeps working
    without changes.
    """

    mode = (os.environ.get("OPSMENDER_DEPLOYMENT_MODE") or "").strip().lower()
    return mode == "development"


async def _self_registration_open(db: AsyncSession) -> bool:
    """Return True iff ``POST /auth/register`` will accept an anonymous caller.

    Open when:
    - deployment mode is ``development`` (covers tests + local dev), OR
    - no users exist (covers a fresh production install that hasn't
      run the bootstrap-admin env vars).
    """

    if _is_development_mode():
        return True
    existing = await UserRepo.list_all(db, limit=1)
    return not existing


class RegistrationOpenResponse(BaseModel):
    open: bool


@router.get(
    "/registration-open",
    response_model=RegistrationOpenResponse,
    summary="Whether anonymous /auth/register will succeed",
)
async def registration_open(
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint so the login page can hide the register link
    when self-signup is closed.

    Sprint 56: in production with at least one user present,
    self-registration returns 403 — admins must use the Admin → People
    invite flow instead.
    """

    return RegistrationOpenResponse(open=await _self_registration_open(db))


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    # Sprint 56: self-registration is closed in production once any
    # user exists. The bootstrap-admin env vars + admin invite flow
    # are the supported paths.
    if not await _self_registration_open(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Self-registration is closed. Ask an admin to send you an "
                "invite from the People page."
            ),
        )

    # Check for existing username / email
    if await UserRepo.get_by_username(db, body.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' already taken",
        )
    if await UserRepo.get_by_email(db, body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.email}' already registered",
        )

    # First user in the system gets admin role automatically
    existing_users = await UserRepo.list_all(db)
    role = "admin" if len(existing_users) == 0 else body.role

    # Multi-tenancy: Ensure at least one organization exists
    from backend.db.repos import OrganizationRepo
    orgs = await OrganizationRepo.list_all(db)
    if not orgs:
        # Create default organization if none exists
        org = await OrganizationRepo.create(db, name="Main", slug="main")
    else:
        org = orgs[0]

    user = await UserRepo.create(
        db,
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=role,
        primary_org_id=org.id,
    )
    
    # Link user to the organization
    await UserRepo.add_to_organization(db, user_id=user.id, org_id=org.id, role=role)
    
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and receive a JWT",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await UserRepo.get_by_username(db, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    token = create_access_token(user.id, user.role)
    return TokenResponse(access_token=token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(user: User = Depends(get_current_user)):
    return user


@router.get(
    "/users",
    response_model=UserListResponse,
    dependencies=[Depends(require_role("admin", "operator"))],
    summary="List all users (admin/operator only)",
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    users = await UserRepo.list_all(db, limit=limit, offset=offset)
    return UserListResponse(items=list(users), total=len(users))


@router.get(
    "/me/organizations",
    response_model=MyOrganizationListResponse,
    summary="List organizations the current user belongs to",
)
async def my_organizations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await UserRepo.list_organizations(db, user.id)
    items = [
        MyOrganizationResponse(
            id=r["id"],
            name=r["name"],
            slug=r["slug"],
            branding=r["branding"],
            role=r["role"],
            is_primary=(user.primary_org_id == r["id"]),
        )
        for r in rows
    ]
    return MyOrganizationListResponse(items=items, total=len(items))


@router.put(
    "/me/primary-org/{org_id}",
    response_model=UserResponse,
    summary="Set the current user's primary (active) organization",
)
async def set_my_primary_org(
    org_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await UserRepo.is_member(db, user.id, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this organization.",
        )
    await UserRepo.set_primary_org(db, user.id, org_id)
    await db.commit()
    await db.refresh(user)
    return user
