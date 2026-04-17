"""Base adapter interface for external incident ingestion.

Every provider adapter must subclass ``IngestAdapter`` and implement
``parse()``.  The return is a normalized ``ParsedIncident`` that the
ingest service uses to create or update an incident in AIM.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any


@dataclasses.dataclass
class ParsedIncident:
    """Normalized incident data extracted from an external payload."""

    title: str
    description: str
    severity: str | None = None  # critical | high | medium | low
    external_id: str | None = None  # unique within the source
    external_source: str | None = None  # e.g. "cloudwatch", "legacy_alert_vendor"
    status: str = "open"  # open | resolved


class IngestAdapter(ABC):
    """Abstract base for provider-specific payload parsers."""

    # Human-readable label, e.g. "CloudWatch Alarms via SNS"
    label: str = "unknown"

    # Provider key matching ``ingest_tokens.provider``
    provider_key: str = "unknown"

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        """Parse an inbound JSON payload into a ``ParsedIncident``.

        Raise ``ValueError`` with a human-readable message if the
        payload is malformed or unsupported.
        """
        ...
