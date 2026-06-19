"""Wave 2 Phase 2 observability and error-ingest adapter tests."""

from __future__ import annotations

import pytest

from backend.ingest.adapters.appdynamics import AppDynamicsAdapter
from backend.ingest.adapters.bugsnag import BugsnagAdapter
from backend.ingest.adapters.dynatrace import DynatraceAdapter
from backend.ingest.adapters.elastic_watcher import ElasticWatcherAdapter
from backend.ingest.adapters.honeycomb import HoneycombAdapter
from backend.ingest.adapters.loki import LokiAdapter
from backend.ingest.adapters.rollbar import RollbarAdapter
from backend.ingest.registry import list_providers


@pytest.mark.parametrize(
    ("adapter", "firing", "resolved", "source", "external_id", "severity"),
    [
        (
            RollbarAdapter(),
            {
                "event_name": "new_item",
                "data": {
                    "item": {
                        "id": 42,
                        "title": "Database timeout",
                        "level": "critical",
                        "status": "active",
                        "project_id": 7,
                        "environment": "production",
                    }
                },
            },
            {
                "event_name": "resolved_item",
                "data": {
                    "item": {
                        "id": 42,
                        "title": "Database timeout",
                        "level": "critical",
                        "status": "resolved",
                    }
                },
            },
            "rollbar",
            "42",
            "critical",
        ),
        (
            BugsnagAdapter(),
            {
                "project": {"name": "Checkout"},
                "trigger": {"type": "firstException"},
                "error": {
                    "id": "event-1",
                    "errorId": "error-1",
                    "exceptionClass": "NoMethodError",
                    "message": "Unable to connect",
                    "context": "checkout#create",
                    "severity": "error",
                    "status": "open",
                },
            },
            {
                "project": {"name": "Checkout"},
                "trigger": {
                    "type": "errorState-ManualChange",
                    "stateChange": "fixed",
                },
                "error": {
                    "errorId": "error-1",
                    "exceptionClass": "NoMethodError",
                    "severity": "error",
                    "status": "fixed",
                },
            },
            "bugsnag",
            "error-1",
            "high",
        ),
        (
            ElasticWatcherAdapter(),
            {
                "watch_id": "error-rate",
                "title": "Elevated error rate",
                "status": "firing",
                "severity": "warning",
                "payload": {"hits": {"total": 27}},
            },
            {
                "watch_id": "error-rate",
                "title": "Elevated error rate",
                "status": "resolved",
                "severity": "warning",
                "payload": {"hits": {"total": 0}},
            },
            "elastic_watcher",
            "error-rate",
            "medium",
        ),
        (
            HoneycombAdapter(),
            {
                "ID": "trigger-1",
                "Name": "Checkout latency",
                "Environment": "production",
                "Severity": "critical",
                "Alert": {
                    "InstanceID": "firing-1",
                    "Status": "TRIGGERED",
                    "Summary": "p99 exceeded two seconds",
                },
            },
            {
                "ID": "trigger-1",
                "Name": "Checkout latency",
                "Environment": "production",
                "Severity": "critical",
                "Alert": {
                    "InstanceID": "firing-1",
                    "Status": "OK",
                    "Summary": "p99 returned to normal",
                },
            },
            "honeycomb",
            "trigger-1",
            "critical",
        ),
        (
            DynatraceAdapter(),
            {
                "ProblemID": "999",
                "PID": "DT-999",
                "ProblemTitle": "Checkout unavailable",
                "ProblemImpact": "APPLICATION",
                "ProblemSeverity": "AVAILABILITY",
                "State": "OPEN",
                "ImpactedEntity": "checkout-api",
                "ProblemURL": "https://dynatrace.example/problems/999",
            },
            {
                "ProblemID": "999",
                "PID": "DT-999",
                "ProblemTitle": "Checkout unavailable",
                "ProblemImpact": "APPLICATION",
                "ProblemSeverity": "AVAILABILITY",
                "State": "RESOLVED",
            },
            "dynatrace",
            "999",
            "critical",
        ),
        (
            AppDynamicsAdapter(),
            {
                "event_guid": "event-open",
                "event_id": "101",
                "incident_id": "health-77",
                "event_name": "Checkout health rule violated",
                "event_type": "POLICY_OPEN_CRITICAL",
                "app_name": "Checkout",
                "severity": "ERROR",
                "summary": "Error rate above threshold",
            },
            {
                "event_guid": "event-close",
                "event_id": "102",
                "incident_id": "health-77",
                "event_name": "Checkout health rule cleared",
                "event_type": "HEALTH_RULE_VIOLATION_ENDED",
                "app_name": "Checkout",
                "severity": "ERROR",
                "summary": "Error rate normal",
            },
            "appdynamics",
            "health-77",
            "high",
        ),
        (
            LokiAdapter(),
            {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "fingerprint": "abc123",
                        "labels": {
                            "alertname": "CheckoutErrorLogs",
                            "severity": "warning",
                        },
                        "annotations": {
                            "summary": "Checkout errors detected",
                            "description": "Log error count exceeded threshold",
                        },
                    }
                ],
            },
            {
                "status": "resolved",
                "alerts": [
                    {
                        "status": "resolved",
                        "fingerprint": "abc123",
                        "labels": {
                            "alertname": "CheckoutErrorLogs",
                            "severity": "warning",
                        },
                        "annotations": {
                            "summary": "Checkout errors cleared",
                        },
                    }
                ],
            },
            "loki",
            "abc123",
            "medium",
        ),
    ],
)
def test_phase2_adapters_map_firing_and_resolved_payloads(
    adapter, firing, resolved, source, external_id, severity
):
    opened = adapter.parse(firing)
    cleared = adapter.parse(resolved)

    assert opened.external_source == source
    assert opened.external_id == external_id
    assert opened.severity == severity
    assert opened.status == "open"
    assert cleared.external_source == source
    assert cleared.external_id == external_id
    assert cleared.status == "resolved"


@pytest.mark.parametrize(
    "adapter",
    [
        RollbarAdapter(),
        BugsnagAdapter(),
        ElasticWatcherAdapter(),
        HoneycombAdapter(),
        DynatraceAdapter(),
        AppDynamicsAdapter(),
        LokiAdapter(),
    ],
)
def test_phase2_adapters_reject_payloads_without_stable_identity(adapter):
    with pytest.raises(ValueError):
        adapter.parse({})


def test_phase2_providers_are_registered():
    providers = {item["key"] for item in list_providers()}
    assert {
        "rollbar",
        "bugsnag",
        "elastic_watcher",
        "honeycomb",
        "dynatrace",
        "appdynamics",
        "loki",
    } <= providers
