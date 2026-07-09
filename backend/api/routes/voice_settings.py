"""Workspace Voice/SMS settings."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ChannelAvailabilityResponse,
    VoiceSettingsResponse,
    VoiceSettingsUpdate,
)
from backend.auth.secrets import encrypt_secret
from backend.db.models import User
from backend.db.repos import AuditEntryRepo, OrgVoiceSettingsRepo
from backend.paging.voice_settings import resolve_voice_settings

router = APIRouter(tags=["voice-settings"])


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


async def _response(db: AsyncSession, org_id: uuid.UUID) -> VoiceSettingsResponse:
    row = await OrgVoiceSettingsRepo.get_for_org(db, org_id)
    resolved = await resolve_voice_settings(db, org_id)
    if row is not None:
        return VoiceSettingsResponse(
            configured=resolved is not None,
            enabled=bool(row.enabled),
            account_sid=row.account_sid or "",
            auth_token_set=bool(row.auth_token_encrypted),
            sms_from_number=row.sms_from_number or "",
            voice_from_number=row.voice_from_number,
            source=resolved.source if resolved is not None else None,
        )
    if resolved is None:
        return VoiceSettingsResponse()
    return VoiceSettingsResponse(
        configured=True,
        enabled=True,
        account_sid=resolved.account_sid,
        auth_token_set=True,
        sms_from_number=resolved.sms_from_number,
        voice_from_number=resolved.voice_from_number,
        source=resolved.source,
    )


@router.get("/api/v1/voice-settings", response_model=VoiceSettingsResponse)
async def get_voice_settings(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    return await _response(db, org_id)


@router.put("/api/v1/voice-settings", response_model=VoiceSettingsResponse)
async def put_voice_settings(
    body: VoiceSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await OrgVoiceSettingsRepo.get_for_org(db, org_id)
    auth_token_encrypted: str | None
    if body.auth_token is None:
        auth_token_encrypted = None
    else:
        token = body.auth_token.strip()
        auth_token_encrypted = encrypt_secret(token) if token else ""

    row = await OrgVoiceSettingsRepo.upsert(
        db,
        org_id,
        account_sid=(
            _clean(body.account_sid)
            if body.account_sid is not None
            else (existing.account_sid if existing is not None else None)
        ),
        auth_token_encrypted=auth_token_encrypted,
        sms_from_number=(
            _clean(body.sms_from_number)
            if body.sms_from_number is not None
            else (existing.sms_from_number if existing is not None else None)
        ),
        voice_from_number=(
            _clean(body.voice_from_number)
            if body.voice_from_number is not None
            else (existing.voice_from_number if existing is not None else None)
        ),
        enabled=(
            bool(body.enabled)
            if body.enabled is not None
            else (bool(existing.enabled) if existing is not None else True)
        ),
    )
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=None,
        tier=0,
        entry_type="voice_settings_update",
        tool_name="voice_settings",
        tool_parameters={
            "actor_user_id": str(user.id),
            "enabled": row.enabled,
            "account_sid_set": bool(row.account_sid),
            "auth_token_set": bool(row.auth_token_encrypted),
            "sms_from_number_set": bool(row.sms_from_number),
            "voice_from_number_set": bool(row.voice_from_number),
        },
        result={"ok": True},
        permitted=True,
    )
    await db.commit()
    return await _response(db, org_id)


@router.get(
    "/api/v1/paging/channel-availability",
    response_model=ChannelAvailabilityResponse,
)
async def get_channel_availability(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    settings = await resolve_voice_settings(db, org_id)
    available = settings is not None
    return ChannelAvailabilityResponse(sms=available, voice=available)
