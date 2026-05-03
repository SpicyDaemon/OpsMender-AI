"""Auth routes — register and login.

POST /auth/register — create a new user
POST /auth/login    — authenticate and receive a JWT
GET  /auth/me       — return the current user profile
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    RegisterRequest,
    TokenResponse,
    UserListResponse,
    UserResponse,
)
from backend.db.models import User
from backend.db.repos import UserRepo

router = APIRouter(prefix="/auth", tags=["auth"])


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
