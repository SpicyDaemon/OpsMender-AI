"""Adapter registry — maps platform key -> adapter instance."""

from __future__ import annotations

from .base import BotConnectorAdapter

_ADAPTERS: dict[str, BotConnectorAdapter] = {}


def register_adapter(adapter: BotConnectorAdapter) -> None:
    _ADAPTERS[adapter.platform] = adapter


def get_adapter(platform: str) -> BotConnectorAdapter | None:
    return _ADAPTERS.get(platform)


def list_platforms() -> list[str]:
    return sorted(_ADAPTERS.keys())
