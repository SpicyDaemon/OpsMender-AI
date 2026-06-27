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

from fastapi import APIRouter, Depends, Form, Response
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import _auth_config
from backend.api.deps import get_db
from backend.db.repos import IncidentAssignmentRepo, IncidentRepo

router = APIRouter(prefix="/paging/voice", tags=["voice"])

# A live page is stale well before this; keep the ack window tight.
_TOKEN_TTL_SECONDS = 1800


def encode_voice_ack_token(
    *, org_id: uuid.UUID, incident_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    """Mint a short-lived signed token authorizing a single voice acknowledgement."""
    cfg = _auth_config()
    now = datetime.now(timezone.utc)
    payload = {
        "org_id": str(org_id),
        "incident_id": str(incident_id),
        "user_id": str(user_id),
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
    Digits: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Twilio ``<Gather>`` action callback. Press ``1`` acknowledges the incident."""
    try:
        payload = _decode_voice_ack_token(token)
    except JWTError:
        return _twiml("This acknowledgement link is invalid or has expired. Goodbye.")

    if Digits.strip() != "1":
        return _twiml("No acknowledgement was recorded. Goodbye.")

    org_id = uuid.UUID(payload["org_id"])
    incident_id = uuid.UUID(payload["incident_id"])
    user_id = uuid.UUID(payload["user_id"])

    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        return _twiml("That incident could not be found. Goodbye.")
    if incident.acknowledged_at is not None:
        return _twiml("This incident was already acknowledged. Goodbye.")

    await IncidentAssignmentRepo.assign(
        db, org_id, incident_id=incident_id, user_id=user_id, assigned_by="self_ack"
    )
    await db.commit()
    return _twiml("Incident acknowledged. You are now the owner. Goodbye.")
