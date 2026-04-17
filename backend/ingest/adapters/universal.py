"""Universal adapter — one ingest endpoint for any alerting tool.

This adapter attempts to extract standard incident fields from arbitrary
JSON payloads without per-provider code. It works in two layers:

1. **Heuristic pass** — walks the payload looking for common field names
   (``title``, ``alertname``, ``summary``, ``message``, ``severity`` …) at
   the top level and one-level-deep under common envelope keys
   (``alert``, ``data``, ``payload``, ``event`` …). This handles the vast
   majority of webhook schemas today (Datadog, Grafana, Prometheus
   Alertmanager, Sumo Logic, custom scripts).

2. **LLM fallback signal** — when the heuristics cannot find a usable
   title or severity, the adapter marks ``needs_llm=True`` on the result.
   The ingest service then invokes an LLM extractor to parse the payload
   from scratch, with the resolved paths cached per payload shape so
   steady-state traffic is free.

The adapter also accepts an optional pre-learned ``field_mapping`` (JSON
paths keyed by ``title``/``severity``/...) which short-circuits the
heuristic walk when the payload shape has been seen before.
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident


# ─── Field-name synonyms ordered by preference ─────────────────────────────

TITLE_KEYS = (
    "title",
    "alertname",
    "alert_name",
    "incident_title",
    "summary",
    "subject",
    "name",
    "monitor_name",
    "check_name",
    "event_type",
    "eventName",
    "display_name",
    "message",
)

DESCRIPTION_KEYS = (
    "description",
    "message",
    "body",
    "text",
    "details",
    "incident_description",
    "alert_body",
    "long_message",
    "summary",
)

SEVERITY_KEYS = (
    "severity",
    "priority",
    "level",
    "alert_severity",
    "event_severity",
    "urgency",
    "importance",
)

EXTERNAL_ID_KEYS = (
    "id",
    "alert_id",
    "incident_id",
    "event_id",
    "monitor_id",
    "fingerprint",
    "key",
    "guid",
    "dedup_key",
    "correlation_id",
    "external_id",
    "uuid",
)

STATUS_KEYS = (
    "status",
    "state",
    "alert_status",
    "monitor_status",
    "event_action",
    "resolution_status",
)

# Envelope keys commonly wrapped around real payload fields
ENVELOPE_KEYS = (
    "alert",
    "data",
    "payload",
    "event",
    "detail",
    "body",
    "message",
    "incident",
    "notification",
)

# Alert-system severity synonyms mapped to AIM's 4-level scale
_SEVERITY_MAP: dict[str, str] = {
    # critical
    "critical": "critical",
    "p1": "critical",
    "sev1": "critical",
    "sev-1": "critical",
    "fatal": "critical",
    "emergency": "critical",
    "page": "critical",
    # high
    "high": "high",
    "p2": "high",
    "sev2": "high",
    "sev-2": "high",
    "error": "high",
    "urgent": "high",
    "major": "high",
    # medium
    "medium": "medium",
    "p3": "medium",
    "sev3": "medium",
    "sev-3": "medium",
    "warning": "medium",
    "warn": "medium",
    "moderate": "medium",
    # low
    "low": "low",
    "p4": "low",
    "p5": "low",
    "sev4": "low",
    "sev-4": "low",
    "info": "low",
    "informational": "low",
    "notice": "low",
    "minor": "low",
    "debug": "low",
}

_RESOLVED_STATUS_TERMS = {
    "resolved",
    "closed",
    "ok",
    "recovery",
    "recovered",
    "cleared",
    "healthy",
    "success",
    "fixed",
}

_INVESTIGATING_STATUS_TERMS = {
    "investigating",
    "acknowledged",
    "ack",
    "in_progress",
    "triggered",
    "alarm",
    "firing",
}


def _resolve_path(data: Any, path: str) -> Any | None:
    """Resolve a dot-separated path in a nested dict / list payload."""
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


def _to_text(value: Any) -> str | None:
    """Flatten a value to a trimmed string, or None if empty."""
    if value is None:
        return None
    if isinstance(value, str):
        out = value.strip()
        return out or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, dict)):
        # Keep short compact reps for lists/dicts so the LLM can still pick them up later
        import json

        text = json.dumps(value, separators=(",", ":"))
        return text if text else None
    return None


def _find_first(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[str, Any] | None:
    """Find the first present key, searching top-level then under common envelopes.

    Returns the (path, value) pair for the first hit.
    """
    # Top-level first — preferred
    for key in keys:
        if key in payload and payload[key] not in (None, "", [], {}):
            return key, payload[key]

    # Case-insensitive top-level match (payloads like "AlertName", "Severity")
    lowered = {k.lower(): k for k in payload.keys() if isinstance(k, str)}
    for key in keys:
        actual = lowered.get(key.lower())
        if actual is not None and payload[actual] not in (None, "", [], {}):
            return actual, payload[actual]

    # One-level-deep inside common envelopes
    for envelope in ENVELOPE_KEYS:
        inner = payload.get(envelope)
        if isinstance(inner, dict):
            for key in keys:
                if key in inner and inner[key] not in (None, "", [], {}):
                    return f"{envelope}.{key}", inner[key]
            inner_lower = {
                k.lower(): k for k in inner.keys() if isinstance(k, str)
            }
            for key in keys:
                actual = inner_lower.get(key.lower())
                if actual is not None and inner[actual] not in (None, "", [], {}):
                    return f"{envelope}.{actual}", inner[actual]
        elif isinstance(inner, list) and inner and isinstance(inner[0], dict):
            # Treat the first array element as the envelope (common in LegacyAlertVendor-like shapes)
            first = inner[0]
            for key in keys:
                if key in first and first[key] not in (None, "", [], {}):
                    return f"{envelope}.0.{key}", first[key]

    return None


def _normalize_severity(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _SEVERITY_MAP:
        return _SEVERITY_MAP[text]
    # Numeric priority: 1 → critical, 2 → high, 3 → medium, 4+ → low
    if text.isdigit():
        n = int(text)
        if n <= 1:
            return "critical"
        if n == 2:
            return "high"
        if n == 3:
            return "medium"
        return "low"
    return None


def _normalize_status(raw: Any) -> str:
    if raw is None:
        return "open"
    text = str(raw).strip().lower()
    if not text:
        return "open"
    if text in _RESOLVED_STATUS_TERMS:
        return "resolved"
    if text in _INVESTIGATING_STATUS_TERMS:
        return "investigating"
    return "open"


class UniversalAdapter(IngestAdapter):
    """Provider-agnostic adapter that works for any webhook JSON.

    Accepts an optional ``field_mapping`` (dot-paths) recorded by the
    ingest service from a previous LLM extraction. When present, that
    mapping is applied first — heuristics run only for unmapped fields.
    """

    label = "Auto-detect (any webhook)"
    provider_key = "auto"

    def __init__(self, field_mapping: dict[str, str] | None = None):
        self._mapping = field_mapping or {}

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        if not isinstance(payload, dict):
            raise ValueError("Universal adapter requires a JSON object payload")

        extracted: dict[str, str] = {}

        def resolve_field(
            field: str,
            keys: tuple[str, ...],
        ) -> Any | None:
            # 1) Pre-learned path
            path = self._mapping.get(field)
            if path:
                value = _resolve_path(payload, path)
                if value not in (None, "", [], {}):
                    extracted[field] = path
                    return value
            # 2) Heuristic discovery
            hit = _find_first(payload, keys)
            if hit is None:
                return None
            resolved_path, value = hit
            extracted[field] = resolved_path
            return value

        title_raw = resolve_field("title", TITLE_KEYS)
        description_raw = resolve_field("description", DESCRIPTION_KEYS)
        severity_raw = resolve_field("severity", SEVERITY_KEYS)
        external_id_raw = resolve_field("external_id", EXTERNAL_ID_KEYS)
        status_raw = resolve_field("status", STATUS_KEYS)

        title = _to_text(title_raw)
        description = _to_text(description_raw)
        severity = _normalize_severity(severity_raw)
        external_id = _to_text(external_id_raw)
        status = _normalize_status(status_raw)

        # Confidence: title must be present and non-generic. If heuristics
        # couldn't find a title at all, fall back to LLM.
        needs_llm = title is None

        # Build a last-resort title so we never produce empty output even
        # if LLM fallback is disabled or fails.
        if title is None:
            title = "Untitled Incident"
        if description is None:
            description = title

        return ParsedIncident(
            title=title,
            description=description,
            severity=severity,
            external_id=external_id,
            external_source="auto",
            status=status,
            needs_llm=needs_llm,
            extracted_paths=extracted or None,
        )
