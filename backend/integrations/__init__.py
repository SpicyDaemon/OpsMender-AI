"""External integration connector framework."""

from backend.integrations import atlassian as atlassian  # noqa: F401
from backend.integrations import azure_devops as azure_devops  # noqa: F401
from backend.integrations import generic as generic  # noqa: F401
from backend.integrations import github as github  # noqa: F401
from backend.integrations import gitlab as gitlab  # noqa: F401
from backend.integrations import linear as linear  # noqa: F401
from backend.integrations import notion as notion  # noqa: F401
from backend.integrations import servicenow as servicenow  # noqa: F401
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
