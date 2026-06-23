"""External integration connector framework."""

from backend.integrations import automation as automation  # noqa: F401
from backend.integrations import atlassian as atlassian  # noqa: F401
from backend.integrations import azure_devops as azure_devops  # noqa: F401
from backend.integrations import cicd as cicd  # noqa: F401
from backend.integrations import generic as generic  # noqa: F401
from backend.integrations import gitea as gitea  # noqa: F401
from backend.integrations import github as github  # noqa: F401
from backend.integrations import gitlab as gitlab  # noqa: F401
from backend.integrations import google_docs as google_docs  # noqa: F401
from backend.integrations import kubernetes as kubernetes  # noqa: F401
from backend.integrations import linear as linear  # noqa: F401
from backend.integrations import notion as notion  # noqa: F401
from backend.integrations import servicenow as servicenow  # noqa: F401
from backend.integrations import support as support  # noqa: F401
from backend.integrations import statuspage as statuspage  # noqa: F401
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationFieldSpec,
    IntegrationResult,
)
from backend.integrations.registry import (
    config_fields,
    credential_fields_by_auth,
    field_schema,
    get_adapter,
    get_kind,
    list_kinds,
    register_adapter,
)

__all__ = [
    "IntegrationAdapter",
    "IntegrationCapability",
    "IntegrationFieldSpec",
    "IntegrationResult",
    "config_fields",
    "credential_fields_by_auth",
    "field_schema",
    "get_adapter",
    "get_kind",
    "list_kinds",
    "register_adapter",
]
