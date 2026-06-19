"""Integration kind catalog and adapter registry."""

from __future__ import annotations

import dataclasses

from backend.integrations.base import IntegrationAdapter


@dataclasses.dataclass(frozen=True)
class IntegrationKind:
    kind: str
    label: str
    supports_base_url: bool
    auth_types: tuple[str, ...]


_KINDS = {
    item.kind: item
    for item in (
        IntegrationKind("github", "GitHub", True, ("pat", "app")),
        IntegrationKind("gitlab", "GitLab", True, ("pat", "oauth")),
        IntegrationKind("gitea", "Gitea", True, ("pat",)),
        IntegrationKind("bitbucket", "Bitbucket", True, ("pat", "oauth")),
        IntegrationKind("azure_devops", "Azure DevOps", True, ("pat", "oauth")),
        IntegrationKind("jira", "Jira", True, ("pat", "oauth", "basic")),
        IntegrationKind("confluence", "Confluence", True, ("pat", "oauth", "basic")),
        IntegrationKind("servicenow", "ServiceNow", True, ("oauth", "basic")),
        IntegrationKind("linear", "Linear", False, ("api_key", "oauth")),
        IntegrationKind("notion", "Notion", False, ("api_key", "oauth")),
        IntegrationKind("google_docs", "Google Docs", False, ("oauth", "custom")),
        IntegrationKind("jenkins", "Jenkins", True, ("basic", "pat")),
        IntegrationKind("circleci", "CircleCI", True, ("api_key",)),
        IntegrationKind("azure_pipelines", "Azure Pipelines", True, ("pat", "oauth")),
        IntegrationKind("terraform_cloud", "Terraform Cloud", True, ("api_key",)),
        IntegrationKind("argocd", "Argo CD", True, ("pat", "oauth")),
        IntegrationKind("ansible", "Ansible Automation", True, ("pat", "basic")),
        IntegrationKind("statuspage", "Statuspage", True, ("api_key",)),
        IntegrationKind("sentry", "Sentry", True, ("pat", "oauth")),
        IntegrationKind("newrelic", "New Relic", False, ("api_key",)),
        IntegrationKind("splunk", "Splunk", True, ("pat", "basic")),
        IntegrationKind("kubernetes", "Kubernetes", True, ("pat", "custom")),
        IntegrationKind("zendesk", "Zendesk", True, ("basic", "oauth")),
        IntegrationKind("freshservice", "Freshservice", True, ("api_key",)),
        IntegrationKind("asana", "Asana", False, ("pat", "oauth")),
        IntegrationKind(
            "custom", "Custom HTTP", True, ("none", "pat", "api_key", "basic", "custom")
        ),
    )
}
_ADAPTERS: dict[str, IntegrationAdapter] = {}


def register_adapter(adapter: IntegrationAdapter) -> IntegrationAdapter:
    if adapter.kind not in _KINDS:
        raise ValueError(f"Unknown integration kind: {adapter.kind}")
    _ADAPTERS[adapter.kind] = adapter
    return adapter


def get_adapter(kind: str) -> IntegrationAdapter | None:
    return _ADAPTERS.get(kind)


def get_kind(kind: str) -> IntegrationKind | None:
    return _KINDS.get(kind)


def list_kinds() -> list[IntegrationKind]:
    return sorted(_KINDS.values(), key=lambda item: item.label)
