from __future__ import annotations

import ast
from pathlib import Path

import backend.integrations  # noqa: F401 - register bundled adapters
from backend.integrations.registry import (
    base_url_metadata,
    field_schema,
    list_kinds,
)


ROOT = Path(__file__).resolve().parents[1]

SOURCE_FILES = {
    "github": ("backend/integrations/github.py",),
    "gitlab": ("backend/integrations/gitlab.py",),
    "gitea": ("backend/integrations/gitea.py",),
    "bitbucket": ("backend/integrations/atlassian.py",),
    "azure_devops": ("backend/integrations/azure_devops.py",),
    "jira": (
        "backend/integrations/atlassian.py",
        "backend/integrations/tools.py",
        "backend/api/routes/ticket_sync.py",
    ),
    "confluence": ("backend/integrations/atlassian.py",),
    "servicenow": (
        "backend/integrations/servicenow.py",
        "backend/integrations/tools.py",
        "backend/api/routes/ticket_sync.py",
    ),
    "linear": ("backend/integrations/linear.py",),
    "notion": ("backend/integrations/notion.py",),
    "google_docs": (
        "backend/integrations/google_docs.py",
        "backend/integrations/google_auth.py",
    ),
    "jenkins": ("backend/integrations/cicd.py",),
    "circleci": ("backend/integrations/cicd.py",),
    "azure_pipelines": ("backend/integrations/cicd.py",),
    "terraform_cloud": ("backend/integrations/automation.py",),
    "argocd": ("backend/integrations/automation.py",),
    "ansible": ("backend/integrations/automation.py",),
    "statuspage": ("backend/integrations/statuspage.py",),
    "kubernetes": ("backend/integrations/kubernetes.py",),
    "zendesk": ("backend/integrations/support.py",),
    "freshservice": ("backend/integrations/support.py",),
    "asana": ("backend/integrations/support.py",),
    "custom": ("backend/integrations/generic.py",),
}

CATALOG_ONLY_KINDS = {"sentry", "newrelic", "splunk"}


def _read_keys(paths: tuple[str, ...]) -> tuple[set[str], set[str]]:
    auth_keys: set[str] = set()
    config_keys: set[str] = set()
    for relative_path in paths:
        tree = ast.parse((ROOT / relative_path).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            key_node = node.args[0]
            if not isinstance(key_node, ast.Constant) or not isinstance(
                key_node.value, str
            ):
                continue
            target = node.func.value
            if isinstance(target, ast.Name) and target.id == "auth":
                auth_keys.add(key_node.value)
            elif (
                isinstance(target, ast.Attribute)
                and target.attr == "config"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"connector", "row"}
            ):
                config_keys.add(key_node.value)
            elif isinstance(target, ast.Name) and target.id == "config":
                config_keys.add(key_node.value)
    return auth_keys, config_keys


def test_every_catalog_kind_has_a_complete_per_auth_schema():
    for definition in list_kinds():
        helper, placeholder = base_url_metadata(definition.kind)
        if definition.supports_base_url:
            assert helper
            assert placeholder
        seen_config: tuple[str, ...] | None = None
        for auth_type in definition.auth_types:
            credential_fields, config_fields = field_schema(definition.kind, auth_type)
            assert all(field.group == "credentials" for field in credential_fields)
            assert all(field.group == "config" for field in config_fields)
            assert len({field.name for field in credential_fields}) == len(
                credential_fields
            )
            assert len({field.name for field in config_fields}) == len(config_fields)
            current_config = tuple(field.name for field in config_fields)
            if seen_config is None:
                seen_config = current_config
            else:
                assert current_config == seen_config


def test_schema_field_names_are_read_by_their_adapter_paths():
    catalog_kinds = {definition.kind for definition in list_kinds()}
    assert set(SOURCE_FILES) | CATALOG_ONLY_KINDS == catalog_kinds

    for definition in list_kinds():
        if definition.kind in CATALOG_ONLY_KINDS:
            continue
        auth_keys, config_keys = _read_keys(SOURCE_FILES[definition.kind])
        for auth_type in definition.auth_types:
            credential_fields, config_fields = field_schema(definition.kind, auth_type)
            assert {field.name for field in credential_fields} <= auth_keys, (
                definition.kind,
                auth_type,
                auth_keys,
            )
            assert {field.name for field in config_fields} <= config_keys, (
                definition.kind,
                config_keys,
            )


def test_unknown_kind_and_unsupported_auth_type_fail_closed():
    try:
        field_schema("missing", "pat")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown integration kind must fail")

    try:
        field_schema("github", "basic")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported auth type must fail")
