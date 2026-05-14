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
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.api.deps import get_db
from backend.db.models import User
from backend.db.repos import OrganizationDomainRepo, UserRepo

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
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        payload,
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
    try:
        payload = decode_access_token(token)
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exc
        user_id = uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exc

    user = await UserRepo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise credentials_exc
    return user


async def get_current_org(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_org_id: str | None = Header(default=None, alias="X-Org-ID"),
    host: str | None = Header(default=None, alias="Host"),
    x_forwarded_host: str | None = Header(default=None, alias="X-Forwarded-Host"),
) -> uuid.UUID:
    """Dependency — returns the active organization ID for the request.

    Resolution order:
    1. **Host header** — if the request hostname is registered in
       ``organization_domains``, that org is *pinned* for the request.
       The authenticated user must be a member or the request is 403.
       Pinned hosts ignore ``X-Org-ID`` so a tenant subdomain cannot
       be subverted by a header from a malicious client.
    2. ``X-Org-ID`` request header — opt-in switching when the deployment
       is single-host; must reference an org the user belongs to.
    3. ``user.primary_org_id`` — the user's persisted default.

    Raises 400 if none yield a valid org.
    """
    # 1. Host pin (X-Forwarded-Host beats Host so reverse-proxy deployments work).
    raw_host = x_forwarded_host or host
    if raw_host:
        match = await OrganizationDomainRepo.find_by_host(db, raw_host)
        if match is not None:
            if not await UserRepo.is_member(db, user.id, match.org_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User is not a member of the organization for this host.",
                )
            return match.org_id

    # 2. X-Org-ID header.
    if x_org_id:
        try:
            requested = uuid.UUID(x_org_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Org-ID header.",
            )
        if not await UserRepo.is_member(db, user.id, requested):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a member of the requested organization.",
            )
        return requested

    # 3. Primary org fallback.
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
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not in allowed roles: {allowed_roles}",
            )
        return user

    return _checker
