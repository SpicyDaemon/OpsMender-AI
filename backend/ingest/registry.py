"""Adapter registry — maps provider keys to adapter classes."""

from __future__ import annotations

from backend.ingest.adapters.base import IngestAdapter
from backend.ingest.adapters.appdynamics import AppDynamicsAdapter
from backend.ingest.adapters.cloudwatch import CloudWatchAdapter
from backend.ingest.adapters.azure_monitor import AzureMonitorAdapter
from backend.ingest.adapters.bugsnag import BugsnagAdapter
from backend.ingest.adapters.dynatrace import DynatraceAdapter
from backend.ingest.adapters.elastic_watcher import ElasticWatcherAdapter
from backend.ingest.adapters.gcp_monitoring import GCPMonitoringAdapter
from backend.ingest.adapters.honeycomb import HoneycombAdapter
from backend.ingest.adapters.loki import LokiAdapter
from backend.ingest.adapters.oci_monitoring import OCIMonitoringAdapter
from backend.ingest.adapters.newrelic import NewRelicAdapter
from backend.ingest.adapters.rollbar import RollbarAdapter
from backend.ingest.adapters.sentry import SentryAdapter
from backend.ingest.adapters.splunk import SplunkAdapter
from backend.ingest.adapters.generic import GenericAdapter
from backend.ingest.adapters.universal import UniversalAdapter

_ADAPTERS: dict[str, type[IngestAdapter]] = {
    "auto": UniversalAdapter,
    "cloudwatch": CloudWatchAdapter,
    "azure_monitor": AzureMonitorAdapter,
    "gcp_monitoring": GCPMonitoringAdapter,
    "oci_monitoring": OCIMonitoringAdapter,
    "sentry": SentryAdapter,
    "newrelic": NewRelicAdapter,
    "splunk": SplunkAdapter,
    "rollbar": RollbarAdapter,
    "bugsnag": BugsnagAdapter,
    "elastic_watcher": ElasticWatcherAdapter,
    "honeycomb": HoneycombAdapter,
    "dynatrace": DynatraceAdapter,
    "appdynamics": AppDynamicsAdapter,
    "loki": LokiAdapter,
    "generic": GenericAdapter,
}


def get_adapter(
    provider: str,
    *,
    field_mapping: dict[str, str] | None = None,
) -> IngestAdapter:
    """Return an adapter instance for the given provider key.

    Falls back to ``UniversalAdapter`` for unknown providers. When
    ``field_mapping`` is supplied it is passed through to adapters that
    support learned-path injection (universal + generic).
    """
    cls = _ADAPTERS.get(provider, UniversalAdapter)
    if cls in (UniversalAdapter, GenericAdapter):
        return cls(field_mapping=field_mapping)
    return cls()


def list_providers() -> list[dict[str, str]]:
    """Return all registered provider adapters with labels.

    ``auto`` is listed first so UIs surface it as the default.
    """
    return [{"key": key, "label": cls.label} for key, cls in _ADAPTERS.items()]
