"""Generic JSON adapter with explicit field mapping.

For tools not covered by the built-in adapters (Grafana, Datadog,
Prometheus Alertmanager, custom scripts), this adapter maps arbitrary
JSON fields to AIM incident fields using a simple dot-path config.

Field mapping is configured per-token via the ``field_mapping``
parameter, with sensible defaults that work for common webhook formats.
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

# Default field paths that cover common webhook formats
_DEFAULT_MAPPING = {
    "title": "title",
    "description": "description",
    "severity": "severity",
    "external_id": "id",
    "status": "status",
}


def _resolve_path(data: dict[str, Any], path: str) -> Any | None:
    """Resolve a dot-separated path like ``alert.title`` in a nested dict."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


class GenericAdapter(IngestAdapter):
    label = "Generic JSON"
    provider_key = "generic"

    def __init__(self, field_mapping: dict[str, str] | None = None):
        self._mapping = {**_DEFAULT_MAPPING, **(field_mapping or {})}

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        title = _resolve_path(payload, self._mapping.get("title", "title"))
        description = _resolve_path(
            payload, self._mapping.get("description", "description")
        )
        severity = _resolve_path(
            payload, self._mapping.get("severity", "severity")
        )
        external_id = _resolve_path(
            payload, self._mapping.get("external_id", "id")
        )
        status_raw = _resolve_path(
            payload, self._mapping.get("status", "status")
        )

        if not title:
            # Fall back to common alternative field names
            title = (
                _resolve_path(payload, "alertname")
                or _resolve_path(payload, "alert.title")
                or _resolve_path(payload, "summary")
                or _resolve_path(payload, "name")
                or "Untitled Incident"
            )

        if not description:
            description = (
                _resolve_path(payload, "message")
                or _resolve_path(payload, "body")
                or _resolve_path(payload, "text")
                or str(title)
            )

        # Normalize severity
        if isinstance(severity, str):
            severity = severity.lower()
            if severity not in ("critical", "high", "medium", "low"):
                severity = "medium"
        else:
            severity = None

        # Normalize status
        status = "open"
        if isinstance(status_raw, str):
            status_lower = status_raw.lower()
            if status_lower in ("resolved", "closed", "ok", "recovery"):
                status = "resolved"
            elif status_lower in ("investigating", "acknowledged"):
                status = "investigating"

        return ParsedIncident(
            title=str(title),
            description=str(description),
            severity=severity,
            external_id=str(external_id) if external_id else None,
            external_source="generic",
            status=status,
        )
