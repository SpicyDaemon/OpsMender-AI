"""Adapter registry — maps provider keys to adapter classes."""

from __future__ import annotations

from backend.ingest.adapters.base import IngestAdapter
from backend.ingest.adapters.cloudwatch import CloudWatchAdapter
from backend.ingest.adapters.azure_monitor import AzureMonitorAdapter
from backend.ingest.adapters.legacy_alert_vendor import LegacyAlertVendorAdapter
from backend.ingest.adapters.generic import GenericAdapter
from backend.ingest.adapters.legacy_alert_relay import LegacyAlertRelayAdapter

_ADAPTERS: dict[str, type[IngestAdapter]] = {
    "cloudwatch": CloudWatchAdapter,
    "azure_monitor": AzureMonitorAdapter,
    "legacy_alert_vendor": LegacyAlertVendorAdapter,
    "legacy_alert_relay": LegacyAlertRelayAdapter,
    "generic": GenericAdapter,
}


def get_adapter(provider: str) -> IngestAdapter:
    """Return an adapter instance for the given provider key.

    Falls back to ``GenericAdapter`` for unknown providers.
    """
    cls = _ADAPTERS.get(provider, GenericAdapter)
    return cls()


def list_providers() -> list[dict[str, str]]:
    """Return all registered provider adapters with labels."""
    return [
        {"key": key, "label": cls.label}
        for key, cls in _ADAPTERS.items()
    ]
