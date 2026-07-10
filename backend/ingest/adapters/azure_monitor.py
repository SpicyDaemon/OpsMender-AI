"""Azure Monitor common alert schema v2 adapter.

Parses the common alert schema and maps severity + status to OpsMender fields.
Reference: https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-common-schema
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import (
    AvailabilitySignal,
    IngestAdapter,
    ParsedIncident,
)

# Azure Monitor severity: 0=Critical, 1=Error, 2=Warning, 3=Informational, 4=Verbose
_SEVERITY_MAP = {
    "Sev0": "critical",
    "Sev1": "high",
    "Sev2": "medium",
    "Sev3": "low",
    "Sev4": "low",
}

_STATUS_MAP = {
    "Activated": "open",
    "Fired": "open",
    "Resolved": "resolved",
    "Deactivated": "resolved",
}


class AzureMonitorAdapter(IngestAdapter):
    label = "Azure Monitor Alerts"
    provider_key = "azure_monitor"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        # Common alert schema
        essentials = payload.get("data", {}).get("essentials", {})
        if not essentials:
            # Try top-level essentials (some webhook configs)
            essentials = payload.get("essentials", {})
        if not essentials:
            raise ValueError(
                "Missing 'data.essentials' or 'essentials' block "
                "in Azure Monitor alert payload"
            )

        alert_id = essentials.get("alertId", "")
        alert_rule = essentials.get("alertRule", "Unknown Alert")
        severity_raw = essentials.get("severity", "Sev2")
        monitor_condition = essentials.get("monitorCondition", "Fired")
        description = essentials.get("description", "No description")
        target_resource = essentials.get("alertTargetIDs", [])
        fired_time = essentials.get("firedDateTime", "")

        title = f"[Azure Monitor] {alert_rule}"
        desc_text = (
            f"**Alert Rule:** {alert_rule}\n"
            f"**Severity:** {severity_raw}\n"
            f"**Condition:** {monitor_condition}\n"
            f"**Description:** {description}\n"
            f"**Target Resources:** {', '.join(target_resource) if target_resource else 'N/A'}\n"
            f"**Fired At:** {fired_time}"
        )

        severity = _SEVERITY_MAP.get(severity_raw, "medium")
        status = _STATUS_MAP.get(monitor_condition, "open")

        # Emit availability signal — Fired=down, Resolved=up
        availability = AvailabilitySignal(
            target_name=alert_rule,
            up=(monitor_condition in ("Resolved", "Deactivated")),
            source="azure_monitor",
        )

        return ParsedIncident(
            title=title,
            description=desc_text,
            severity=severity,
            external_id=alert_id or alert_rule,
            external_source="azure_monitor",
            status=status,
            availability=availability,
        )
