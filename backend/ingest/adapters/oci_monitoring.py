"""Oracle Cloud Infrastructure (OCI) Monitoring alarm adapter.

Parses OCI Monitoring alarm webhook notifications.
Reference: https://docs.oracle.com/en-us/iaas/Content/Monitoring/Concepts/monitoringoverview.htm

OCI sends alarm payloads with this structure::

    {
        "type": "CHRONOS_NOTIFICATION",
        "data": {
            "alarmId": "ocid1.alarm.oc1...",
            "alarmName": "High CPU Utilization",
            "severity": "CRITICAL",
            "status": "FIRING",           // FIRING | OK | RESET
            "timestamp": "2026-05-01T00:00:00Z",
            "body": "Alarm is in a FIRING state...",
            "alarmMetaData": [
                {
                    "status": "FIRING",
                    "severity": "CRITICAL",
                    "namespace": "oci_computeagent",
                    "query": "CpuUtilization[1m].mean() > 80",
                    "dimensions": {
                        "resourceId": "ocid1.instance...",
                        "region": "us-phoenix-1"
                    }
                }
            ]
        }
    }
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import AvailabilitySignal, IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "ERROR": "high",
    "WARNING": "medium",
    "INFO": "low",
}

_STATUS_MAP = {
    "FIRING": "open",
    "OK": "resolved",
    "RESET": "resolved",
}


class OCIMonitoringAdapter(IngestAdapter):
    label = "Oracle Cloud (OCI) Monitoring"
    provider_key = "oci_monitoring"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        data = payload.get("data")
        if not isinstance(data, dict):
            # Some OCI configs send the data fields at top level
            data = payload

        alarm_name = data.get("alarmName")
        if not alarm_name:
            raise ValueError(
                "Missing 'alarmName' in OCI Monitoring alarm payload"
            )

        alarm_id = data.get("alarmId", "")
        status_raw = data.get("status", "FIRING")
        severity_raw = data.get("severity", "WARNING")
        body = data.get("body", "")
        data.get("timestamp", "")

        # Extract resource info from alarmMetaData
        meta_list = data.get("alarmMetaData", [])
        namespace = ""
        query = ""
        region = ""
        resource_id = ""
        if meta_list and isinstance(meta_list[0], dict):
            meta = meta_list[0]
            namespace = meta.get("namespace", "")
            query = meta.get("query", "")
            dims = meta.get("dimensions", {})
            region = dims.get("region", "")
            resource_id = dims.get("resourceId", "")

        title = f"[OCI] {alarm_name} — {status_raw}"
        desc_text = (
            f"**Alarm:** {alarm_name}\n"
            f"**Status:** {status_raw}\n"
            f"**Severity:** {severity_raw}\n"
            f"**Namespace:** {namespace}\n"
            f"**Query:** {query}\n"
        )
        if region:
            desc_text += f"**Region:** {region}\n"
        if resource_id:
            desc_text += f"**Resource:** {resource_id}\n"
        if body:
            desc_text += f"**Details:** {body}\n"

        severity = _SEVERITY_MAP.get(severity_raw, "medium")
        status = _STATUS_MAP.get(status_raw, "open")

        # Fingerprint: alarm ID or alarm name + region
        external_id = alarm_id or f"{alarm_name}:{region}"

        # Availability signal — FIRING=down, OK/RESET=up
        availability = AvailabilitySignal(
            target_name=alarm_name,
            up=(status_raw in ("OK", "RESET")),
            source="oci_monitoring",
        )

        return ParsedIncident(
            title=title,
            description=desc_text,
            severity=severity,
            external_id=external_id,
            external_source="oci_monitoring",
            status=status,
            availability=availability,
        )
