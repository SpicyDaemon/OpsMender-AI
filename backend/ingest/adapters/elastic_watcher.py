"""Elastic Watcher and OpenSearch monitor webhook adapter."""

from __future__ import annotations

import json
from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "error": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


class ElasticWatcherAdapter(IngestAdapter):
    label = "Elastic / OpenSearch Watcher"
    provider_key = "elastic_watcher"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        context = payload.get("ctx") or payload.get("context") or {}
        monitor = payload.get("monitor") or context.get("monitor") or {}
        trigger = payload.get("trigger") or context.get("trigger") or {}
        alert = payload.get("alert") or context.get("alert") or {}
        watch_id = (
            payload.get("watch_id")
            or payload.get("watchId")
            or context.get("watch_id")
            or monitor.get("id")
            or alert.get("id")
        )
        if not watch_id:
            raise ValueError("Missing Elastic/OpenSearch watch or monitor id")
        title = (
            payload.get("title")
            or alert.get("name")
            or trigger.get("name")
            or monitor.get("name")
            or str(watch_id)
        )
        state = str(
            payload.get("status")
            or payload.get("state")
            or alert.get("state")
            or alert.get("status")
            or "firing"
        ).lower()
        resolved = state in {
            "resolved",
            "ok",
            "inactive",
            "completed",
        }
        severity_raw = str(
            payload.get("severity")
            or alert.get("severity")
            or trigger.get("severity")
            or "high"
        ).lower()
        details = payload.get("payload") or context.get("payload") or alert
        rendered = json.dumps(details, default=str, sort_keys=True)
        if len(rendered) > 2000:
            rendered = rendered[:1999] + "…"
        return ParsedIncident(
            title=f"[Elastic/OpenSearch] {title}",
            description=f"**State:** {state}\n**Payload:** `{rendered}`",
            severity=_SEVERITY.get(severity_raw, "high"),
            external_id=str(watch_id),
            external_source="elastic_watcher",
            status="resolved" if resolved else "open",
        )
