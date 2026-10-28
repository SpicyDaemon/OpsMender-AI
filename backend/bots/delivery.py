"""Shared delivery/update primitives for Notification Channels."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryReceipt:
    """Normalized provider receipt for an outbound notification message."""

    ok: bool = True
    error: str | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    external_channel_id: str | None = None
    can_update: bool = False
    # The receipt status to record; None keeps the caller's default.
    delivery_status: str | None = None
    # The provider did not take it but may later (429, 408, 5xx, timeout,
    # network), so the caller holds the card instead of failing the channel.
    retryable: bool = False


@dataclass(frozen=True)
class UpdateResult:
    """Normalized result for an in-place message update attempt."""

    ok: bool
    error: str | None = None
    receipt: DeliveryReceipt | None = None
    fallback_to_followup: bool = False


@dataclass(frozen=True)
class IncidentActionDescriptor:
    """Future adapter-facing descriptor for secure incident controls."""

    action: str
    label: str
    token: str
    url: str | None = None
