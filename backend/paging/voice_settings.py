"""Resolve per-organization Voice/SMS settings with environment fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import httpx
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
            _clean(env.get("OPSMENDER_TWILIO_VOICE_FROM_NUMBER")) or sms_from_number
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


async def verify_twilio_credentials(
    settings: ResolvedVoiceSettings,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, str]:
    """Validate Twilio credentials by fetching the account resource with them.

    Returns ``(ok, message)`` and never raises — a self-test button should fail
    softly. ``client`` is injectable for tests (e.g. an ``httpx.MockTransport``).
    """
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.account_sid}.json"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        resp = await client.get(url, auth=(settings.account_sid, settings.auth_token))
    except httpx.HTTPError as exc:
        return False, f"Could not reach Twilio: {exc}"
    finally:
        if owns_client:
            await client.aclose()
    if resp.status_code == 200:
        friendly = ""
        try:
            friendly = (resp.json() or {}).get("friendly_name") or ""
        except ValueError:
            friendly = ""
        return True, (
            f"Twilio credentials are valid (account: {friendly})."
            if friendly
            else "Twilio credentials are valid."
        )
    if resp.status_code in (401, 403):
        return (
            False,
            "Twilio rejected the credentials — check the Account SID and Auth Token.",
        )
    return False, f"Twilio returned HTTP {resp.status_code}."


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
