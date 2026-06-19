"""BugSnag error webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {"error": "high", "warning": "medium", "info": "low"}
_RESOLVED = {"fixed", "snoozed", "ignored"}


class BugsnagAdapter(IngestAdapter):
    label = "BugSnag Error Notifications"
    provider_key = "bugsnag"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        error = payload.get("error")
        trigger = payload.get("trigger") or {}
        if not isinstance(error, dict):
            raise ValueError("Missing BugSnag error payload")
        error_id = error.get("errorId") or error.get("id")
        if not error_id:
            raise ValueError("Missing BugSnag errorId")
        state_change = str(trigger.get("stateChange") or "").lower()
        error_status = str(error.get("status") or "open").lower()
        status = (
            "resolved"
            if error_status in _RESOLVED or state_change in _RESOLVED
            else "open"
        )
        severity = str(error.get("severity") or "error").lower()
        project = payload.get("project") or {}
        title = (
            " — ".join(
                part
                for part in (
                    str(error.get("exceptionClass") or ""),
                    str(error.get("message") or ""),
                )
                if part
            )
            or "BugSnag error"
        )
        lines = [
            f"**Project:** {project.get('name') or 'unknown'}",
            f"**Trigger:** {trigger.get('type') or 'error notification'}",
            f"**Status:** {error_status}",
            f"**Context:** {error.get('context') or 'unknown'}",
        ]
        if error.get("url"):
            lines.append(f"**Error:** {error['url']}")
        return ParsedIncident(
            title=f"[BugSnag] {title}",
            description="\n".join(lines),
            severity=_SEVERITY.get(severity, "high"),
            external_id=str(error_id),
            external_source="bugsnag",
            status=status,
        )
