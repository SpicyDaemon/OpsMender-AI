"""Outbound webhook triggers for session lifecycle changes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.repos import IncidentRepo, SessionRepo, WebhookTriggerRepo

log = logging.getLogger(__name__)

SESSION_TRIGGER_EVENTS = {
    "*",
    "session.created",
    "session.awaiting_approval",
    "session.active",
    "session.completed",
    "session.failed",
    "session.timed_out",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float = 10.0,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.post(url, json=payload, headers=headers)


def _track_task(
    task_registry: set[asyncio.Task] | None,
    coro,
) -> asyncio.Task:
    task = asyncio.create_task(coro)
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


def _event_label(event_type: str) -> str:
    return event_type.replace("session.", "").replace("_", " ").title()


def _severity_label(incident: dict[str, Any] | None) -> str:
    if not incident or not incident.get("severity"):
        return "unknown"
    return str(incident["severity"])


def _build_notification_text(payload: dict[str, Any]) -> str:
    event_label = _event_label(payload["event"])
    session = payload["session"]
    incident = payload.get("incident")
    incident_title = incident["title"] if incident else "No linked incident"
    severity = _severity_label(incident)
    summary = session.get("summary") or "No summary yet."
    return (
        f"AIM {event_label}: {incident_title} "
        f"(severity: {severity}, tier: {session['tier']}, status: {session['status']}). "
        f"{summary}"
    )


def _format_slack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session = payload["session"]
    incident = payload.get("incident")
    text = _build_notification_text(payload)
    fields = [
        {
            "type": "mrkdwn",
            "text": f"*Event*\n{_event_label(payload['event'])}",
        },
        {
            "type": "mrkdwn",
            "text": f"*Status*\n{session['status']}",
        },
        {
            "type": "mrkdwn",
            "text": f"*Tier*\n{session['tier']}",
        },
    ]
    if incident and incident.get("severity"):
        fields.append(
            {
                "type": "mrkdwn",
                "text": f"*Severity*\n{incident['severity']}",
            }
        )

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*AIM {_event_label(payload['event'])}*\n{text}",
            },
        },
        {
            "type": "section",
            "fields": fields,
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Session `{session['id']}`"
                        + (
                            f" • Incident `{incident['id']}`"
                            if incident and incident.get("id")
                            else ""
                        )
                    ),
                }
            ],
        },
    ]
    return {
        "text": text,
        "blocks": blocks,
    }


def _format_teams_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": _build_notification_text(payload),
    }


def _format_payload_for_trigger(trigger, payload: dict[str, Any]) -> dict[str, Any]:
    trigger_format = str(getattr(trigger, "format", "generic") or "generic").lower()
    if trigger_format == "slack":
        return _format_slack_payload(payload)
    if trigger_format == "teams":
        return _format_teams_payload(payload)
    return payload


async def _build_session_payload(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str,
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    async with session_factory() as db:
        session = await SessionRepo.get_by_id(db, session_id)
        if session is None:
            return None
        incident = None
        if session.incident_id is not None:
            incident = await IncidentRepo.get_by_id(db, session.incident_id)

    return {
        "event": event_type,
        "source": "aim",
        "sent_at": _utcnow().isoformat(),
        "session": {
            "id": str(session.id),
            "incident_id": str(session.incident_id) if session.incident_id else None,
            "tier": int(session.tier),
            "model_provider": session.model_provider,
            "model_id": session.model_id,
            "status": session.status,
            "summary": session.summary,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        },
        "incident": (
            None
            if incident is None
            else {
                "id": str(incident.id),
                "title": incident.title,
                "description": incident.description,
                "status": incident.status,
                "severity": incident.severity,
                "external_id": incident.external_id,
                "external_source": incident.external_source,
            }
        ),
    }


def _request_headers(trigger) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for key, value in (trigger.headers or {}).items():
        headers[str(key)] = str(value)
    if trigger.token:
        headers.setdefault("Authorization", f"Bearer {trigger.token}")
    return headers


async def _record_delivery_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger_id: uuid.UUID,
    error: str | None,
) -> None:
    async with session_factory() as db:
        await WebhookTriggerRepo.mark_delivery(db, trigger_id, error=error)
        await db.commit()


async def _deliver_payload_to_trigger(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger,
    payload: dict[str, Any],
    event_type: str,
) -> tuple[bool, str, int | None]:
    payload = _format_payload_for_trigger(trigger, payload)
    try:
        response = await post_json(
            trigger.url,
            payload=payload,
            headers=_request_headers(trigger),
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        await _record_delivery_result(
            session_factory,
            trigger_id=trigger.id,
            error=detail,
        )
        log.warning(
            "webhook trigger delivery failed for %s (%s): %s",
            trigger.name,
            event_type,
            detail,
        )
        return False, detail, getattr(getattr(exc, "response", None), "status_code", None)

    await _record_delivery_result(session_factory, trigger_id=trigger.id, error=None)
    return True, f"Delivered to {trigger.url}", response.status_code


async def deliver_session_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str,
    session_id: uuid.UUID,
) -> None:
    if event_type not in SESSION_TRIGGER_EVENTS:
        raise ValueError(f"Unsupported webhook trigger event: {event_type}")

    payload = await _build_session_payload(
        session_factory,
        event_type=event_type,
        session_id=session_id,
    )
    if payload is None:
        return

    async with session_factory() as db:
        triggers = list(await WebhookTriggerRepo.list_matching_event(db, event_type))

    for trigger in triggers:
        await _deliver_payload_to_trigger(
            session_factory,
            trigger=trigger,
            payload=payload,
            event_type=event_type,
        )


def schedule_session_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task_registry: set[asyncio.Task] | None,
    event_type: str,
    session_id: uuid.UUID,
) -> asyncio.Task:
    return _track_task(
        task_registry,
        deliver_session_event(
            session_factory,
            event_type=event_type,
            session_id=session_id,
        ),
    )


async def deliver_test_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger_id: uuid.UUID,
) -> tuple[bool, str, int | None, str]:
    async with session_factory() as db:
        trigger = await WebhookTriggerRepo.get_by_id(db, trigger_id)
    if trigger is None:
        return False, "Webhook trigger not found", None, "webhook.test"

    payload = {
        "event": "webhook.test",
        "source": "aim",
        "sent_at": _utcnow().isoformat(),
        "test": True,
        "session": {
            "id": "test-session",
            "incident_id": None,
            "tier": 2,
            "model_provider": "test",
            "model_id": "test",
            "status": "completed",
            "summary": "Webhook test payload from AIM.",
            "started_at": _utcnow().isoformat(),
            "ended_at": _utcnow().isoformat(),
        },
        "incident": {
            "id": "test-incident",
            "title": "Webhook test incident",
            "description": "Generated by AIM when testing an outbound trigger.",
            "status": "open",
            "severity": "medium",
            "external_id": None,
            "external_source": None,
        },
        "trigger": {
            "id": str(trigger.id),
            "name": trigger.name,
        },
    }
    success, detail, status_code = await _deliver_payload_to_trigger(
        session_factory,
        trigger=trigger,
        payload=payload,
        event_type="webhook.test",
    )
    return success, detail, status_code, "webhook.test"
