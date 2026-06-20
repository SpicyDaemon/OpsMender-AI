"""TOTP MFA enrollment, verification, recovery, and organization policy."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import (
    create_access_token,
    decode_access_token,
    get_current_org,
    get_current_user,
    require_role,
)
from backend.api.deps import get_db
from backend.api.schemas import (
    MFAConfirmRequest,
    MFAConfirmResponse,
    MFADisableRequest,
    MFASetupResponse,
    MFAStatusResponse,
    MFAVerifyRequest,
    OrganizationMFASettingsResponse,
    OrganizationMFASettingsUpdate,
    TokenResponse,
)
from backend.auth.mfa import (
    find_recovery_code,
    generate_recovery_codes,
    hash_recovery_code,
    matching_totp_counter,
    new_totp_secret,
    provisioning_uri,
    qr_data_url,
)
from backend.auth.secrets import decrypt_secret, encrypt_secret
from backend.db.models import User, UserMFA
from backend.db.repos import OrganizationRepo, UserMFARepo, UserRepo

router = APIRouter(tags=["mfa"])


async def _verify_factor(
    db: AsyncSession,
    row: UserMFA,
    *,
    totp_code: str | None,
    recovery_code: str | None,
) -> bool:
    if bool(totp_code) == bool(recovery_code):
        return False

    if totp_code:
        counter = matching_totp_counter(
            decrypt_secret(row.totp_secret_encrypted),
            totp_code,
        )
        if counter is None or row.last_used_code == f"totp:{counter}":
            return False
        await UserMFARepo.record_totp_use(db, row, counter)
        return True

    index = find_recovery_code(recovery_code or "", list(row.recovery_codes or []))
    if index is None:
        return False
    await UserMFARepo.consume_recovery_code(db, row, index)
    return True


@router.get("/auth/mfa/status", response_model=MFAStatusResponse)
async def mfa_status(
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    row = await UserMFARepo.get(db, user.id)
    org = await OrganizationRepo.get_by_id(db, org_id)
    return MFAStatusResponse(
        enabled=bool(row and row.enabled_at),
        required=bool(org and org.mfa_required and user.auth_source == "local"),
        recovery_codes_remaining=len(row.recovery_codes or []) if row else 0,
    )


@router.post("/auth/mfa/setup", response_model=MFASetupResponse)
async def setup_mfa(
    user: User = Depends(get_current_user),
    _org_id: uuid.UUID = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    if user.auth_source != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA enrollment is available for local accounts only.",
        )
    existing = await UserMFARepo.get(db, user.id)
    if existing is not None and existing.enabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled.",
        )

    secret = new_totp_secret()
    uri = provisioning_uri(secret, email=user.email)
    await UserMFARepo.upsert_pending(
        db,
        user.id,
        totp_secret_encrypted=encrypt_secret(secret),
    )
    await db.commit()
    return MFASetupResponse(
        secret=secret,
        otpauth_url=uri,
        qr_data_url=qr_data_url(uri),
    )


@router.post("/auth/mfa/confirm", response_model=MFAConfirmResponse)
async def confirm_mfa(
    body: MFAConfirmRequest,
    user: User = Depends(get_current_user),
    _org_id: uuid.UUID = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    row = await UserMFARepo.get(db, user.id)
    if row is None or row.enabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Start MFA setup before confirming.",
        )
    secret = decrypt_secret(row.totp_secret_encrypted)
    if matching_totp_counter(secret, body.totp_code) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authenticator code.",
        )

    plaintext = generate_recovery_codes()
    await UserMFARepo.enable(
        db,
        row,
        recovery_codes=[hash_recovery_code(code) for code in plaintext],
    )
    await db.commit()
    return MFAConfirmResponse(recovery_codes=plaintext)


@router.post("/auth/mfa/verify", response_model=TokenResponse)
async def verify_mfa(
    body: MFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = decode_access_token(body.mfa_token)
        if payload.get("token_type") != "mfa":
            raise ValueError("wrong token type")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge.",
        )

    user = await UserRepo.get_by_id(db, user_id)
    row = await UserMFARepo.get(db, user_id)
    if (
        user is None
        or not user.is_active
        or user.deleted_at is not None
        or row is None
        or row.enabled_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge.",
        )
    if not await _verify_factor(
        db,
        row,
        totp_code=body.totp_code,
        recovery_code=body.recovery_code,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authenticator or recovery code.",
        )

    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.role))


@router.delete("/auth/mfa", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    body: MFADisableRequest,
    user: User = Depends(get_current_user),
    _org_id: uuid.UUID = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    row = await UserMFARepo.get(db, user.id)
    if row is None or row.enabled_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MFA is not enabled.",
        )
    if not await _verify_factor(
        db,
        row,
        totp_code=body.totp_code,
        recovery_code=body.recovery_code,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authenticator or recovery code.",
        )
    await UserMFARepo.delete(db, row)
    await db.commit()


@router.patch(
    "/admin/org/settings",
    response_model=OrganizationMFASettingsResponse,
)
async def update_org_mfa_settings(
    body: OrganizationMFASettingsUpdate,
    org_id: uuid.UUID = Depends(get_current_org),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.update(
        db,
        org_id,
        mfa_required=body.mfa_required,
    )
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    await db.commit()
    return OrganizationMFASettingsResponse(
        org_id=org.id,
        mfa_required=org.mfa_required,
    )
