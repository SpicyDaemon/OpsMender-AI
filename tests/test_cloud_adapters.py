"""Tests for GCP, OCI, and Azure Monitor availability signal adapters."""

from __future__ import annotations

import json

import pytest

from backend.ingest.adapters.azure_monitor import AzureMonitorAdapter
from backend.ingest.adapters.gcp_monitoring import GCPMonitoringAdapter
from backend.ingest.adapters.oci_monitoring import OCIMonitoringAdapter
from backend.ingest.registry import list_providers


# ======================================================================
# Azure Monitor — availability signal
# ======================================================================


class TestAzureMonitorAvailability:

    def _make_payload(self, condition: str = "Fired") -> dict:
        return {
            "data": {
                "essentials": {
                    "alertId": "alert-123",
                    "alertRule": "VM-Health-Check",
                    "severity": "Sev0",
                    "monitorCondition": condition,
                    "description": "VM is not responding",
                    "alertTargetIDs": ["/subscriptions/.../vm1"],
                    "firedDateTime": "2026-05-01T00:00:00Z",
                }
            }
        }

    def test_fired_emits_down(self):
        adapter = AzureMonitorAdapter()
        result = adapter.parse(self._make_payload("Fired"))

        assert result.availability is not None
        assert result.availability.target_name == "VM-Health-Check"
        assert result.availability.up is False
        assert result.availability.source == "azure_monitor"

    def test_resolved_emits_up(self):
        adapter = AzureMonitorAdapter()
        result = adapter.parse(self._make_payload("Resolved"))

        assert result.availability is not None
        assert result.availability.up is True

    def test_deactivated_emits_up(self):
        adapter = AzureMonitorAdapter()
        result = adapter.parse(self._make_payload("Deactivated"))

        assert result.availability is not None
        assert result.availability.up is True

    def test_incident_fields_correct(self):
        adapter = AzureMonitorAdapter()
        result = adapter.parse(self._make_payload("Fired"))

        assert result.title == "[Azure Monitor] VM-Health-Check"
        assert result.severity == "critical"  # Sev0
        assert result.status == "open"
        assert result.external_source == "azure_monitor"


# ======================================================================
# GCP Cloud Monitoring
# ======================================================================


class TestGCPMonitoringAdapter:

    def _make_payload(self, state: str = "open") -> dict:
        return {
            "incident": {
                "incident_id": "inc-456",
                "resource_name": "webserver-85",
                "state": state,
                "policy_name": "Webserver-Health",
                "condition_name": "CPU usage",
                "summary": "CPU for webserver-85 is above threshold",
                "started_at": 1385085727,
                "ended_at": None,
                "url": "https://console.cloud.google.com/monitoring/...",
            },
            "version": "1.2",
        }

    def test_open_emits_down(self):
        adapter = GCPMonitoringAdapter()
        result = adapter.parse(self._make_payload("open"))

        assert result.availability is not None
        assert result.availability.target_name == "Webserver-Health"
        assert result.availability.up is False
        assert result.availability.source == "gcp_monitoring"

    def test_closed_emits_up(self):
        adapter = GCPMonitoringAdapter()
        result = adapter.parse(self._make_payload("closed"))

        assert result.availability is not None
        assert result.availability.up is True

    def test_acknowledged_emits_down(self):
        adapter = GCPMonitoringAdapter()
        result = adapter.parse(self._make_payload("acknowledged"))

        assert result.availability is not None
        assert result.availability.up is False

    def test_incident_fields_open(self):
        adapter = GCPMonitoringAdapter()
        result = adapter.parse(self._make_payload("open"))

        assert result.title == "[GCP Monitoring] Webserver-Health — CPU usage"
        assert result.severity == "high"
        assert result.status == "open"
        assert result.external_source == "gcp_monitoring"
        assert result.external_id == "inc-456"

    def test_incident_fields_closed(self):
        adapter = GCPMonitoringAdapter()
        result = adapter.parse(self._make_payload("closed"))

        assert result.status == "resolved"
        assert result.severity == "low"

    def test_missing_incident_raises(self):
        adapter = GCPMonitoringAdapter()
        with pytest.raises(ValueError, match="Missing 'incident'"):
            adapter.parse({"version": "1.2"})

    def test_severity_from_condition(self):
        """If incident contains a severity field, it should be used."""
        adapter = GCPMonitoringAdapter()
        payload = self._make_payload("open")
        payload["incident"]["severity"] = "critical"
        result = adapter.parse(payload)
        assert result.severity == "critical"


# ======================================================================
# Oracle Cloud (OCI) Monitoring
# ======================================================================


class TestOCIMonitoringAdapter:

    def _make_payload(self, status: str = "FIRING") -> dict:
        return {
            "type": "CHRONOS_NOTIFICATION",
            "data": {
                "alarmId": "ocid1.alarm.oc1.phx.abc123",
                "alarmName": "High-CPU-Utilization",
                "severity": "CRITICAL",
                "status": status,
                "timestamp": "2026-05-01T00:00:00Z",
                "body": f"Alarm is in a {status} state",
                "alarmMetaData": [
                    {
                        "status": status,
                        "severity": "CRITICAL",
                        "namespace": "oci_computeagent",
                        "query": "CpuUtilization[1m].mean() > 80",
                        "dimensions": {
                            "resourceId": "ocid1.instance.oc1.phx.xyz",
                            "region": "us-phoenix-1",
                        },
                    }
                ],
            },
        }

    def test_firing_emits_down(self):
        adapter = OCIMonitoringAdapter()
        result = adapter.parse(self._make_payload("FIRING"))

        assert result.availability is not None
        assert result.availability.target_name == "High-CPU-Utilization"
        assert result.availability.up is False
        assert result.availability.source == "oci_monitoring"

    def test_ok_emits_up(self):
        adapter = OCIMonitoringAdapter()
        result = adapter.parse(self._make_payload("OK"))

        assert result.availability is not None
        assert result.availability.up is True

    def test_reset_emits_up(self):
        adapter = OCIMonitoringAdapter()
        result = adapter.parse(self._make_payload("RESET"))

        assert result.availability is not None
        assert result.availability.up is True

    def test_incident_fields_firing(self):
        adapter = OCIMonitoringAdapter()
        result = adapter.parse(self._make_payload("FIRING"))

        assert result.title == "[OCI] High-CPU-Utilization — FIRING"
        assert result.severity == "critical"
        assert result.status == "open"
        assert result.external_source == "oci_monitoring"
        assert "oci_computeagent" in result.description
        assert "us-phoenix-1" in result.description

    def test_incident_fields_ok(self):
        adapter = OCIMonitoringAdapter()
        result = adapter.parse(self._make_payload("OK"))

        assert result.status == "resolved"

    def test_missing_alarm_name_raises(self):
        adapter = OCIMonitoringAdapter()
        with pytest.raises(ValueError, match="Missing 'alarmName'"):
            adapter.parse({"data": {"status": "FIRING"}})

    def test_top_level_data_fallback(self):
        """OCI payloads sometimes have data fields at top level."""
        adapter = OCIMonitoringAdapter()
        result = adapter.parse({
            "alarmId": "alarm-1",
            "alarmName": "Disk-Full",
            "severity": "WARNING",
            "status": "FIRING",
        })
        assert result.title == "[OCI] Disk-Full — FIRING"
        assert result.severity == "medium"  # WARNING maps to medium


# ======================================================================
# Registry — verify new providers are registered
# ======================================================================


class TestRegistryInclusion:

    def test_gcp_in_registry(self):
        providers = {p["key"] for p in list_providers()}
        assert "gcp_monitoring" in providers

    def test_oci_in_registry(self):
        providers = {p["key"] for p in list_providers()}
        assert "oci_monitoring" in providers

    def test_all_major_clouds_present(self):
        providers = {p["key"] for p in list_providers()}
        for key in ("cloudwatch", "azure_monitor", "gcp_monitoring", "oci_monitoring"):
            assert key in providers, f"{key} missing from registry"
