"""Integration kind catalog and adapter registry."""

from __future__ import annotations

import dataclasses

from backend.integrations.base import IntegrationAdapter, IntegrationFieldSpec


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

_BASE_URL_METADATA: dict[str, tuple[str, str]] = {
    "github": (
        "Hosted default: https://api.github.com. For Enterprise Server, enter its API base or instance root.",
        "https://api.github.com",
    ),
    "gitlab": (
        "Hosted default: https://gitlab.com/api/v4. For self-managed GitLab, enter its API base or instance root.",
        "https://gitlab.example.com/api/v4",
    ),
    "gitea": (
        "Required: your Gitea instance URL or API v1 base.",
        "https://gitea.example.com",
    ),
    "bitbucket": (
        "Cloud uses https://api.bitbucket.org/2.0 by default. For Data Center, enter the instance root or REST API base.",
        "https://api.bitbucket.org/2.0",
    ),
    "azure_devops": (
        "Leave blank for Azure DevOps Services, or enter the collection URL for a self-hosted deployment.",
        "https://dev.azure.com/acme",
    ),
    "jira": (
        "Required: your Jira Cloud site or on-premises instance URL.",
        "https://acme.atlassian.net",
    ),
    "confluence": (
        "Required: your Confluence Cloud site or on-premises instance URL.",
        "https://acme.atlassian.net/wiki",
    ),
    "servicenow": (
        "Required: your ServiceNow instance URL.",
        "https://acme.service-now.com",
    ),
    "jenkins": (
        "Required: the Jenkins controller URL.",
        "https://jenkins.example.com",
    ),
    "circleci": (
        "Uses https://circleci.com/api/v2 by default. Enter a CircleCI Server API base when self-hosted.",
        "https://circleci.com/api/v2",
    ),
    "azure_pipelines": (
        "Leave blank for Azure DevOps Services, or enter the collection URL for a self-hosted deployment.",
        "https://dev.azure.com/acme",
    ),
    "terraform_cloud": (
        "Uses https://app.terraform.io/api/v2 by default. Enter a Terraform Enterprise API v2 base when self-hosted.",
        "https://app.terraform.io/api/v2",
    ),
    "argocd": (
        "Required: the Argo CD server URL.",
        "https://argocd.example.com",
    ),
    "ansible": (
        "Required: the AWX or Ansible Automation Controller URL.",
        "https://controller.example.com",
    ),
    "statuspage": (
        "Uses https://api.statuspage.io/v1 by default.",
        "https://api.statuspage.io/v1",
    ),
    "sentry": (
        "Enter the Sentry API base for a self-hosted deployment; leave blank for hosted Sentry.",
        "https://sentry.io/api/0",
    ),
    "splunk": (
        "Required for an outbound Splunk adapter: the Splunk management API base.",
        "https://splunk.example.com:8089",
    ),
    "kubernetes": (
        "Required: the Kubernetes API server URL.",
        "https://cluster.example.com:6443",
    ),
    "zendesk": (
        "Required: your Zendesk subdomain URL.",
        "https://acme.zendesk.com",
    ),
    "freshservice": (
        "Required: your Freshservice portal URL.",
        "https://acme.freshservice.com",
    ),
    "custom": (
        "Required: the HTTP endpoint root that OpsMender should probe.",
        "https://service.example.com",
    ),
}


def _credential(
    name: str,
    label: str,
    *,
    required: bool = True,
    kind: str = "secret",
    helper: str | None = None,
    placeholder: str | None = None,
    doc_url: str | None = None,
    default=None,
) -> IntegrationFieldSpec:
    return IntegrationFieldSpec(
        name=name,
        label=label,
        kind=kind,  # type: ignore[arg-type]
        group="credentials",
        required=required,
        helper=helper,
        placeholder=placeholder,
        doc_url=doc_url,
        default=default,
    )


def _config(
    name: str,
    label: str,
    *,
    required: bool = False,
    kind: str = "text",
    helper: str | None = None,
    placeholder: str | None = None,
    doc_url: str | None = None,
    options: tuple[tuple[str, str], ...] = (),
    default=None,
) -> IntegrationFieldSpec:
    return IntegrationFieldSpec(
        name=name,
        label=label,
        kind=kind,  # type: ignore[arg-type]
        group="config",
        required=required,
        helper=helper,
        placeholder=placeholder,
        doc_url=doc_url,
        options=options,
        default=default,
    )


TOKEN = _credential("token", "Token")
ACCESS_TOKEN = _credential("access_token", "Access token")
API_KEY = _credential("api_key", "API key")
USERNAME = _credential("username", "Username", kind="text")
PASSWORD = _credential("password", "Password")
EMAIL = _credential("email", "Email", kind="text")
API_TOKEN = _credential("api_token", "API token")

_AUTH_FIELDS: dict[str, dict[str, tuple[IntegrationFieldSpec, ...]]] = {
    "github": {
        "pat": (
            _credential(
                "token",
                "Personal access token",
                doc_url="https://github.com/settings/tokens",
            ),
        ),
        "app": (
            _credential("app_id", "App ID", kind="text"),
            _credential("installation_id", "Installation ID", kind="text"),
            _credential(
                "private_key",
                "Private key",
                kind="textarea",
                placeholder="-----BEGIN PRIVATE KEY-----",
            ),
            _credential(
                "installation_token",
                "Installation token",
                required=False,
                helper="Optional cached installation token. Normally OpsMender mints one from the app credentials.",
            ),
        ),
    },
    "gitlab": {"pat": (TOKEN,), "oauth": (ACCESS_TOKEN,)},
    "gitea": {"pat": (TOKEN,)},
    "bitbucket": {
        "pat": (
            _credential(
                "email",
                "Atlassian account email",
                kind="text",
                required=False,
                helper="Used with Bitbucket Cloud API tokens; omit for Data Center bearer tokens.",
            ),
            API_TOKEN,
        ),
        "oauth": (ACCESS_TOKEN,),
    },
    "azure_devops": {"pat": (TOKEN,), "oauth": (ACCESS_TOKEN,)},
    "jira": {
        "pat": (
            _credential(
                "email",
                "Atlassian account email",
                kind="text",
                required=False,
            ),
            API_TOKEN,
            _credential(
                "webhook_secret",
                "Ticket-sync webhook secret",
                required=False,
                helper="Optional HMAC secret for signed inbound Jira ticket-sync webhooks.",
            ),
        ),
        "oauth": (
            ACCESS_TOKEN,
            _credential(
                "webhook_secret",
                "Ticket-sync webhook secret",
                required=False,
                helper="Optional HMAC secret for signed inbound Jira ticket-sync webhooks.",
            ),
        ),
        "basic": (
            USERNAME,
            PASSWORD,
            _credential(
                "webhook_secret",
                "Ticket-sync webhook secret",
                required=False,
                helper="Optional HMAC secret for signed inbound Jira ticket-sync webhooks.",
            ),
        ),
    },
    "confluence": {
        "pat": (EMAIL, API_TOKEN),
        "oauth": (ACCESS_TOKEN,),
        "basic": (USERNAME, PASSWORD),
    },
    "servicenow": {
        "oauth": (
            ACCESS_TOKEN,
            _credential(
                "webhook_token",
                "Ticket-sync webhook token",
                required=False,
                helper="Optional shared token for inbound ServiceNow ticket-sync webhooks.",
            ),
        ),
        "basic": (
            USERNAME,
            PASSWORD,
            _credential(
                "webhook_token",
                "Ticket-sync webhook token",
                required=False,
                helper="Optional shared token for inbound ServiceNow ticket-sync webhooks.",
            ),
        ),
    },
    "linear": {"api_key": (API_KEY,), "oauth": (ACCESS_TOKEN,)},
    "notion": {"api_key": (API_KEY,), "oauth": (ACCESS_TOKEN,)},
    "google_docs": {
        "oauth": (ACCESS_TOKEN,),
        "custom": (
            _credential("client_email", "Service-account email", kind="text"),
            _credential(
                "private_key",
                "Private key",
                kind="textarea",
                placeholder="-----BEGIN PRIVATE KEY-----",
            ),
            _credential(
                "delegated_user",
                "Delegated user",
                kind="text",
                required=False,
                helper="Optional Google Workspace user for domain-wide delegation.",
            ),
            _credential(
                "token_uri",
                "Token URI",
                kind="url",
                required=False,
                placeholder="https://oauth2.googleapis.com/token",
            ),
        ),
    },
    "jenkins": {
        "basic": (USERNAME, PASSWORD),
        "pat": (USERNAME, API_TOKEN),
    },
    "circleci": {"api_key": (API_KEY,)},
    "azure_pipelines": {"pat": (TOKEN,), "oauth": (ACCESS_TOKEN,)},
    "terraform_cloud": {"api_key": (API_KEY,)},
    "argocd": {"pat": (TOKEN,), "oauth": (ACCESS_TOKEN,)},
    "ansible": {"pat": (TOKEN,), "basic": (USERNAME, PASSWORD)},
    "statuspage": {"api_key": (API_KEY,)},
    # These catalog entries currently represent inbound alert providers and do
    # not have outbound adapters yet. Their fields capture what an outbound
    # adapter (or a richer inbound correlation) will need and guide the operator.
    "sentry": {
        "pat": (
            _credential(
                "token",
                "Auth token",
                helper="A Sentry internal-integration or user auth token.",
                doc_url="https://sentry.io/settings/account/api/auth-tokens/",
            ),
        ),
        "oauth": (ACCESS_TOKEN,),
    },
    "newrelic": {
        "api_key": (
            _credential(
                "api_key",
                "User API key",
                helper="A New Relic User key (starts with 'NRAK-').",
                doc_url="https://docs.newrelic.com/docs/apis/intro-apis/new-relic-api-keys/",
            ),
        ),
    },
    "splunk": {
        "pat": (
            _credential(
                "token",
                "Auth token",
                helper="A Splunk authentication (bearer) token for the management API.",
                doc_url="https://docs.splunk.com/Documentation/Splunk/latest/Security/CreateauthtokensonSplunkWeb",
            ),
        ),
        "basic": (USERNAME, PASSWORD),
    },
    "kubernetes": {
        "pat": (
            TOKEN,
            _credential(
                "ca_cert",
                "CA certificate",
                kind="textarea",
                required=False,
                placeholder="-----BEGIN CERTIFICATE-----",
            ),
        ),
        "custom": (
            _credential(
                "headers",
                "Request headers",
                kind="textarea",
                helper="JSON object of HTTP headers sent to the Kubernetes API.",
                placeholder='{"Authorization":"Bearer …"}',
                default={},
            ),
            _credential(
                "ca_cert",
                "CA certificate",
                kind="textarea",
                required=False,
                placeholder="-----BEGIN CERTIFICATE-----",
            ),
        ),
    },
    "zendesk": {
        "basic": (EMAIL, API_TOKEN),
        "oauth": (ACCESS_TOKEN,),
    },
    "freshservice": {"api_key": (API_KEY,)},
    "asana": {"pat": (TOKEN,), "oauth": (ACCESS_TOKEN,)},
    "custom": {
        "none": (),
        "pat": (TOKEN,),
        "api_key": (
            API_KEY,
            _credential(
                "header",
                "Header name",
                kind="text",
                required=False,
                placeholder="X-API-Key",
            ),
        ),
        "basic": (USERNAME, PASSWORD),
        "custom": (),
    },
}

_CONFIG_FIELDS: dict[str, tuple[IntegrationFieldSpec, ...]] = {
    "github": (
        _config("owner", "Default owner", placeholder="acme"),
        _config("repo", "Default repository", placeholder="service"),
        _config(
            "api_version",
            "API version",
            placeholder="2022-11-28",
            default="2022-11-28",
        ),
    ),
    "gitlab": (_config("project", "Default project", placeholder="group/project"),),
    "gitea": (
        _config("owner", "Default owner", placeholder="acme"),
        _config("repo", "Default repository", placeholder="service"),
    ),
    "bitbucket": (
        _config(
            "edition",
            "Edition",
            kind="select",
            options=(("cloud", "Cloud"), ("data_center", "Data Center")),
            default="cloud",
        ),
        _config("workspace", "Cloud workspace", placeholder="acme"),
        _config("project", "Data Center project", placeholder="OPS"),
        _config("repo", "Default repository", placeholder="service"),
    ),
    "azure_devops": (
        _config("organization", "Organization", required=True, placeholder="acme"),
        _config("project", "Default project", placeholder="Operations"),
        _config("repository", "Default repository", placeholder="service"),
    ),
    "jira": (
        _config(
            "edition",
            "Edition",
            kind="select",
            options=(
                ("cloud", "Cloud"),
                ("server", "Server / Data Center"),
            ),
            default="cloud",
        ),
        _config("api_version", "API version", placeholder="3"),
        _config("project_key", "Default project key", placeholder="OPS"),
        _config("issue_type", "Default issue type", placeholder="Task"),
        _config(
            "ticket_sync_enabled",
            "Ticket sync",
            kind="select",
            options=(("false", "Disabled"), ("true", "Enabled")),
            default=False,
        ),
        _config(
            "status_map",
            "Ticket status map",
            kind="textarea",
            helper="JSON object mapping OpsMender statuses to Jira statuses.",
            placeholder='{"open":"To Do","in_progress":"In Progress","resolved":"Done"}',
            default={},
        ),
    ),
    "confluence": (
        _config(
            "edition",
            "Edition",
            kind="select",
            options=(("cloud", "Cloud"), ("on_prem", "On-premises")),
            default="cloud",
        ),
        _config("space_id", "Default space ID", placeholder="OPS"),
    ),
    "servicenow": (
        _config("table", "Default table", placeholder="incident", default="incident"),
        _config(
            "ticket_sync_enabled",
            "Ticket sync",
            kind="select",
            options=(("false", "Disabled"), ("true", "Enabled")),
            default=False,
        ),
        _config(
            "status_map",
            "Ticket status map",
            kind="textarea",
            helper="JSON object mapping OpsMender statuses to ServiceNow states.",
            placeholder='{"open":"1","in_progress":"2","resolved":"6"}',
            default={},
        ),
    ),
    "linear": (_config("team_id", "Default team ID"),),
    "notion": (
        _config("parent_page_id", "Default parent page ID"),
        _config("data_source_id", "Default data source ID"),
        _config(
            "notion_version",
            "Notion API version",
            placeholder="2026-03-11",
            default="2026-03-11",
        ),
    ),
    "google_docs": (),
    "jenkins": (_config("job", "Default job", placeholder="folder/service"),),
    "circleci": (
        _config("project_slug", "Default project slug", placeholder="gh/acme/service"),
    ),
    "azure_pipelines": (
        _config("organization", "Organization", required=True, placeholder="acme"),
        _config("project", "Default project", placeholder="Operations"),
    ),
    "terraform_cloud": (
        _config("organization", "Organization", required=True, placeholder="acme"),
        _config("workspace_id", "Default workspace ID", placeholder="ws-…"),
    ),
    "argocd": (
        _config("application", "Default application", placeholder="production-service"),
    ),
    "ansible": (),
    "statuspage": (_config("page_id", "Page ID", required=True),),
    "sentry": (
        _config(
            "organization",
            "Organization slug",
            required=True,
            placeholder="acme",
            helper="Your Sentry organization slug (the /organizations/<slug>/ path segment).",
        ),
        _config(
            "project",
            "Project slug",
            placeholder="backend",
            helper="Optional default project slug for project-scoped queries.",
        ),
        _config(
            "environment",
            "Environment",
            placeholder="production",
            helper="Optional environment filter (e.g. production, staging).",
        ),
    ),
    "newrelic": (
        _config(
            "account_id",
            "Account ID",
            required=True,
            placeholder="1234567",
            helper="Your New Relic account ID — NerdGraph queries are account-scoped.",
        ),
        _config(
            "region",
            "Region",
            kind="select",
            options=(
                ("us", "US (api.newrelic.com)"),
                ("eu", "EU (api.eu.newrelic.com)"),
            ),
            default="us",
            helper="The data-center region your New Relic account lives in.",
        ),
    ),
    "splunk": (
        _config(
            "index",
            "Default index",
            placeholder="main",
            helper="Optional default index to search.",
        ),
        _config(
            "app",
            "App context",
            placeholder="search",
            helper="Optional Splunk app namespace for searches.",
        ),
    ),
    "kubernetes": (
        _config(
            "namespace", "Default namespace", placeholder="default", default="default"
        ),
        _config(
            "verify_tls",
            "Verify TLS",
            kind="select",
            options=(("true", "Yes"), ("false", "No")),
            default=True,
        ),
    ),
    "zendesk": (),
    "freshservice": (),
    "asana": (_config("project_id", "Default project ID"),),
    "custom": (
        _config(
            "headers",
            "Request headers",
            kind="textarea",
            helper="JSON object of non-secret HTTP headers.",
            placeholder='{"Accept":"application/json"}',
            default={},
        ),
        _config("health_path", "Health path", placeholder="/ready"),
    ),
}


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


def field_schema(
    kind: str,
    auth_type: str,
) -> tuple[tuple[IntegrationFieldSpec, ...], tuple[IntegrationFieldSpec, ...]]:
    """Return credential/config fields for one catalog kind and auth shape."""

    definition = get_kind(kind)
    if definition is None:
        raise KeyError(kind)
    if auth_type not in definition.auth_types:
        raise ValueError(f"Unsupported auth type '{auth_type}' for {kind}")
    return (
        _AUTH_FIELDS.get(kind, {}).get(auth_type, ()),
        _CONFIG_FIELDS.get(kind, ()),
    )


def credential_fields_by_auth(
    kind: str,
) -> dict[str, tuple[IntegrationFieldSpec, ...]]:
    definition = get_kind(kind)
    if definition is None:
        raise KeyError(kind)
    return {
        auth_type: field_schema(kind, auth_type)[0]
        for auth_type in definition.auth_types
    }


def config_fields(kind: str) -> tuple[IntegrationFieldSpec, ...]:
    definition = get_kind(kind)
    if definition is None:
        raise KeyError(kind)
    return _CONFIG_FIELDS.get(kind, ())


def base_url_metadata(kind: str) -> tuple[str | None, str | None]:
    definition = get_kind(kind)
    if definition is None:
        raise KeyError(kind)
    return _BASE_URL_METADATA.get(kind, (None, None))
