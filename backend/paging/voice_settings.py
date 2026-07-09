"""Resolve per-organization Voice/SMS settings with environment fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.secrets import decrypt_secret
from backend.db.repos import OrgVoiceSettingsRepo
from backend.paging.channels import SMSChannel, VoiceChannel


@dataclass(frozen=True)
class ResolvedVoiceSettings:
    account_sid: str
    auth_token: str
    sms_from_number: str
    voice_from_number: str
    voice_status_callback_url: str | None = None
    source: str = "environment"


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _env_settings(env: Mapping[str, str]) -> ResolvedVoiceSettings | None:
    account_sid = _clean(env.get("OPSMENDER_TWILIO_ACCOUNT_SID"))
    auth_token = _clean(env.get("OPSMENDER_TWILIO_AUTH_TOKEN"))
    sms_from_number = _clean(env.get("OPSMENDER_TWILIO_FROM_NUMBER"))
    if not account_sid or not auth_token or not sms_from_number:
        return None
    return ResolvedVoiceSettings(
        account_sid=account_sid,
        auth_token=auth_token,
        sms_from_number=sms_from_number,
        voice_from_number=(
            _clean(env.get("OPSMENDER_TWILIO_VOICE_FROM_NUMBER"))
            or sms_from_number
        ),
        voice_status_callback_url=_clean(
            env.get("OPSMENDER_TWILIO_VOICE_STATUS_CALLBACK_URL")
        ),
        source="environment",
    )


async def resolve_voice_settings(
    db: AsyncSession,
    org_id,
    env: Mapping[str, str] | None = None,
) -> ResolvedVoiceSettings | None:
    row = await OrgVoiceSettingsRepo.get_for_org(db, org_id)
    if (
        row is not None
        and row.enabled
        and row.account_sid
        and row.auth_token_encrypted
        and row.sms_from_number
    ):
        return ResolvedVoiceSettings(
            account_sid=row.account_sid,
            auth_token=decrypt_secret(row.auth_token_encrypted),
            sms_from_number=row.sms_from_number,
            voice_from_number=row.voice_from_number or row.sms_from_number,
            source="database",
        )
    return _env_settings(env or os.environ)


def build_sms_channel(settings: ResolvedVoiceSettings) -> SMSChannel:
    return SMSChannel(
        account_sid=settings.account_sid,
        auth_token=settings.auth_token,
        from_number=settings.sms_from_number,
    )


def build_voice_channel(settings: ResolvedVoiceSettings) -> VoiceChannel:
    return VoiceChannel(
        account_sid=settings.account_sid,
        auth_token=settings.auth_token,
        from_number=settings.voice_from_number,
        status_callback_url=settings.voice_status_callback_url,
    )
