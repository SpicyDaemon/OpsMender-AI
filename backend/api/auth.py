"""JWT authentication and password hashing for AIM.

Provides:
- Password hashing via ``bcrypt`` directly (passlib has compatibility
  issues with bcrypt >= 4.1 on Python 3.12+)
- JWT token creation/validation via ``python-jose``
- FastAPI dependencies: ``get_current_user``, ``require_role``

Configuration is driven by environment variables:
- ``AIM_JWT_SECRET``  — signing key (required in production)
- ``AIM_JWT_ALGORITHM`` — default ``HS256``
- ``AIM_JWT_EXPIRE_MINUTES`` — default ``60``
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.db.models import User
from backend.db.repos import UserRepo

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get("AIM_JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = os.environ.get("AIM_JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.environ.get("AIM_JWT_EXPIRE_MINUTES", "60"))

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
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.  Raises ``JWTError`` on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


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
