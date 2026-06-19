"""External integration connector framework."""

from backend.integrations import generic as generic  # noqa: F401
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.registry import (
    get_adapter,
    get_kind,
    list_kinds,
    register_adapter,
)

__all__ = [
    "IntegrationAdapter",
    "IntegrationCapability",
    "IntegrationResult",
    "get_adapter",
    "get_kind",
    "list_kinds",
    "register_adapter",
]
