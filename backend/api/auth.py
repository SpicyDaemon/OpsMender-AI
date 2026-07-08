"""JWT authentication and password hashing for OpsMender.

Provides:
- Password hashing via ``bcrypt`` directly (passlib has compatibility
  issues with bcrypt >= 4.1 on Python 3.12+)
- JWT token creation/validation via ``python-jose``
- FastAPI dependencies: ``get_current_user``, ``require_role``

Configuration is driven by environment variables:
- ``OPSMENDER_JWT_SECRET``  — signing key (required in production)
- ``OPSMENDER_JWT_ALGORITHM`` — default ``HS256``
- ``OPSMENDER_JWT_EXPIRE_MINUTES`` — default ``60``
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.api_tokens import API_TOKEN_PREFIX, hash_api_token
from backend.config_loader import AppConfig
from backend.api.deps import get_db
from backend.db.models import User
from backend.db.repos import ApiTokenRepo, UserRepo

def _auth_config():
    return AppConfig.load().auth

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly — passlib broken on Python 3.12+)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` if *plain* matches *hashed*."""
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: uuid.UUID,
    role: str,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT containing ``sub`` (user_id) and ``role``."""
    settings = _auth_config()
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "token_type": "access",
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def create_mfa_token(
    user_id: uuid.UUID,
    role: str,
    *,
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    """Create a short-lived JWT that can only complete an MFA challenge."""
    settings = _auth_config()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "token_type": "mfa",
            "iat": now,
            "exp": now + expires_delta,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.  Raises ``JWTError`` on failure."""
    settings = _auth_config()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )


# ---------------------------------------------------------------------------
# OAuth2 scheme  (extracts token from Authorization header)
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — returns the authenticated ``User`` or raises 401."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token.startswith(API_TOKEN_PREFIX):
        token_row = await ApiTokenRepo.get_active_by_hash(db, hash_api_token(token))
        if token_row is None:
            raise credentials_exc
        user = await UserRepo.get_by_id(db, token_row.created_by)
        if user is None or not user.is_active or user.deleted_at is not None:
            raise credentials_exc
        now = datetime.now(timezone.utc)
        last_used = token_row.last_used_at
        if last_used is None or now - _aware(last_used) >= timedelta(minutes=1):
            token_row.last_used_at = now
            await db.flush()
        setattr(user, "api_token_name", token_row.name)
        setattr(user, "effective_role", token_row.role)
        return user

    try:
        payload = decode_access_token(token)
        if payload.get("token_type") not in (None, "access"):
            raise credentials_exc
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exc
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exc

    user = await UserRepo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise credentials_exc
    setattr(user, "api_token_name", None)
    setattr(user, "effective_role", user.role)
    return user


async def get_current_org(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Dependency — returns the active organization ID for the request.

    OpsMender now runs as a single-workspace instance. ``org_id`` remains in
    the schema as an internal boundary, but requests no longer switch orgs via
    headers or host names. The authenticated user's primary org is the one
    workspace context for the request.
    """
    if user.primary_org_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have an active organization context."
        )
    return user.primary_org_id


def require_role(*allowed_roles: str):
    """Return a dependency that enforces role membership.

    Usage::

        @router.post("/dangerous", dependencies=[Depends(require_role("admin"))])
        async def dangerous(): ...

    Or inject the user directly::

        @router.post("/op")
        async def op(user: User = Depends(require_role("admin", "operator"))):
            ...
    """

    async def _checker(user: User = Depends(get_current_user)) -> User:
        effective_role = getattr(user, "effective_role", user.role)
        if effective_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{effective_role}' not in allowed roles: {allowed_roles}",
            )
        return user

    return _checker


def actor_label(user: User) -> str:
    token_name = getattr(user, "api_token_name", None)
    if token_name:
        return f"api-token:{token_name}"
    return str(user.id)


async def reject_api_tokens(user: User = Depends(get_current_user)) -> User:
    if getattr(user, "api_token_name", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API tokens are not accepted for this endpoint",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
