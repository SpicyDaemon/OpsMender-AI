"""Twilio Programmable Voice — acknowledge a phone page from the keypad (DTMF).

A voice page speaks the incident and then ``<Gather>``s a single digit. Pressing
``1`` POSTs back here with a short-lived signed token identifying the incident +
responder, and the incident is acknowledged (a self-ack assignment, same as the
"acknowledge the page" button in the UI).

The token is the bearer credential: unguessable, single-purpose, and expiring,
so the endpoint needs no session auth — Twilio cannot present one. Only
OpsMender (holding the JWT secret) can mint a valid token, and it is scoped to
exactly one incident + responder for a short window.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import _auth_config
from backend.api.deps import get_db
from backend.db.repos import IncidentAssignmentRepo, IncidentRepo

router = APIRouter(prefix="/paging/voice", tags=["voice"])

# A live page is stale well before this; keep the ack window tight.
_TOKEN_TTL_SECONDS = 1800


def encode_voice_ack_token(
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    user_id: uuid.UUID,
    summary: str = "",
) -> str:
    """Mint a short-lived signed token authorizing a single voice acknowledgement.

    ``summary`` (truncated) is carried so the keypad "repeat" option can re-read
    the page without extra server state.
    """
    cfg = _auth_config()
    now = datetime.now(timezone.utc)
    payload = {
        "org_id": str(org_id),
        "incident_id": str(incident_id),
        "user_id": str(user_id),
        "summary": summary[:240],
        "purpose": "voice_ack",
        "iat": now,
        "exp": now + timedelta(seconds=_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def _decode_voice_ack_token(token: str) -> dict:
    cfg = _auth_config()
    payload = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    if payload.get("purpose") != "voice_ack":
        raise JWTError("not a voice_ack token")
    return payload


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _twiml(say: str) -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{_xml_escape(say)}</Say><Hangup/></Response>"
    )
    return Response(content=body, media_type="application/xml")


@router.post("/ack/{token}")
async def voice_ack(
    token: str,
    request: Request,
    Digits: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Twilio ``<Gather>`` callback: 1 = acknowledge, 2 = escalate, 3 = resolve,
    * = repeat."""
    try:
        payload = _decode_voice_ack_token(token)
    except JWTError:
        return _twiml("This acknowledgement link is invalid or has expired. Goodbye.")

    org_id = uuid.UUID(payload["org_id"])
    incident_id = uuid.UUID(payload["incident_id"])
    user_id = uuid.UUID(payload["user_id"])
    digit = Digits.strip()

    # Repeat: re-read the menu (relative action posts back to this same URL).
    if digit == "*":
        from backend.paging.page_text import format_voice_menu_twiml

        twiml = format_voice_menu_twiml(
            payload.get("summary") or "Incident page.", f"/paging/voice/ack/{token}"
        )
        return Response(content=twiml, media_type="application/xml")

    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        return _twiml("That incident could not be found. Goodbye.")

    if digit == "1":
        if incident.acknowledged_at is not None:
            return _twiml("This incident was already acknowledged. Goodbye.")
        await IncidentAssignmentRepo.assign(
            db, org_id, incident_id=incident_id, user_id=user_id, assigned_by="self_ack"
        )
        await db.commit()
        return _twiml("Incident acknowledged. You are now the owner. Goodbye.")

    if digit == "2":
        from backend.paging.channel_factory import build_channel_factory
        from backend.paging.escalation import escalate_now

        result = await escalate_now(
            db,
            org_id,
            incident_id=incident_id,
            channel_factory=build_channel_factory(),
        )
        await db.commit()
        if result is None:
            return _twiml(
                "There are no further responders to escalate to. Goodbye."
            )
        return _twiml("Escalating to the next responder. Goodbye.")

    if digit == "3":
        if incident.status == "resolved":
            return _twiml("This incident was already resolved. Goodbye.")
        from backend.api.session_runner import stop_incident_sessions
        from backend.services.incident_timeline import record_lifecycle_comment

        incident.status = "resolved"
        await db.flush()
        # Stop any AI sessions still working a now-resolved incident.
        await stop_incident_sessions(
            request.app,
            db,
            org_id,
            incident_id,
            reason="Incident resolved from a phone page",
        )
        await record_lifecycle_comment(
            db,
            org_id,
            incident_id=incident_id,
            body="Resolved the incident from a phone page.",
            author_user_id=user_id,
        )
        await db.commit()
        # Notify chat channels after commit (best-effort), mirroring the UI path.
        from backend.api.routes.incidents import _notify_channels

        await _notify_channels(db, incident_id, org_id, "incident.resolved")
        return _twiml("Incident resolved. Goodbye.")

    return _twiml("No acknowledgement was recorded. Goodbye.")
