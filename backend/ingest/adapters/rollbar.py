"""Rollbar item-notification webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "critical": "critical",
    "error": "high",
    "warning": "medium",
    "info": "low",
    "debug": "low",
}


class RollbarAdapter(IngestAdapter):
    label = "Rollbar Item Notifications"
    provider_key = "rollbar"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        event_name = str(payload.get("event_name") or "").lower()
        item = (payload.get("data") or {}).get("item") or payload.get("item")
        if not isinstance(item, dict):
            raise ValueError("Missing Rollbar data.item payload")
        item_id = item.get("id") or item.get("counter")
        if item_id is None:
            raise ValueError("Missing Rollbar item id")
        occurrence = item.get("last_occurrence") or {}
        message = (occurrence.get("body") or {}).get("message") or {}
        title = item.get("title") or message.get("body") or "Rollbar item"
        level = str(item.get("level") or "error").lower()
        item_status = str(item.get("status") or "").lower()
        status = (
            "resolved"
            if event_name == "resolved_item" or item_status == "resolved"
            else "open"
        )
        description = "\n".join(
            (
                f"**Event:** {event_name or 'item notification'}",
                f"**Project:** {item.get('project_id') or item.get('project') or 'unknown'}",
                f"**Level:** {level}",
                f"**Environment:** {item.get('environment') or 'unknown'}",
            )
        )
        return ParsedIncident(
            title=f"[Rollbar] {title}",
            description=description,
            severity=_SEVERITY.get(level, "high"),
            external_id=str(item_id),
            external_source="rollbar",
            status=status,
        )
