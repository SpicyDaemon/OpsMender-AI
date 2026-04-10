"""Auth routes — register and login.

POST /auth/register — create a new user
POST /auth/login    — authenticate and receive a JWT
GET  /auth/me       — return the current user profile
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.api.deps import get_db
from backend.api.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
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

    user = await UserRepo.create(
        db,
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=role,
    )
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
