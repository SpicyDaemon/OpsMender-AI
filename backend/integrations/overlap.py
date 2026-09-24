"""Advisory detection of overlapping tool sources on one service.

A service can allowlist an MCP server and a native integration connector that
both reach the same external system (an Atlassian MCP server and a native Jira
connector, say). Nothing arbitrates between them: both tool surfaces reach the
model, which chooses by description text. The routes are not equivalent:
``IntegrationToolRuntime.call_tool`` links a filed ticket to the incident and
starts ticket sync, while the MCP route does neither.

This module only *detects* that situation so the service view and
``opsmender doctor`` can warn. It never changes which tools a session gets.
Matching is heuristic, so a false positive must never block anything.

Only the MCP server's name, command, args, and URL are inspected. Environment
variables and tokens are never read, because they can hold secrets.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

# Extra identity phrases for connector kinds whose MCP servers are commonly
# named after the vendor or an abbreviation rather than the kind itself. The
# kind always counts too (``azure_devops`` matches "azure-devops" and
# "azuredevops").
_KIND_ALIASES: dict[str, tuple[str, ...]] = {
    "jira": ("atlassian",),
    "confluence": ("atlassian",),
    "kubernetes": ("k8s", "kubectl"),
    "azure_pipelines": ("azure devops",),
    "newrelic": ("new relic",),
    "argocd": ("argo cd",),
    "servicenow": ("service now",),
    "circleci": ("circle ci",),
    "terraform_cloud": ("terraform",),
    "google_docs": ("google drive", "gdrive", "google workspace"),
}

# Kinds that name a transport rather than a system; they never overlap.
_NEVER_OVERLAPS = frozenset({"custom"})

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclasses.dataclass(frozen=True)
class ToolSourceOverlap:
    """An enabled connector and an active MCP server that look like one system."""

    kind: str
    connector_id: uuid.UUID
    connector_name: str
    mcp_server_id: uuid.UUID
    mcp_server_name: str
    matched_term: str


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _identity_phrases(kind: str) -> list[tuple[str, ...]]:
    kind = (kind or "").strip().lower()
    if not kind or kind in _NEVER_OVERLAPS:
        return []
    phrases = [tuple(_tokens(kind))]
    phrases.extend(tuple(_tokens(alias)) for alias in _KIND_ALIASES.get(kind, ()))
    return [phrase for phrase in phrases if phrase]


def _server_tokens(server: Any) -> list[str]:
    args = getattr(server, "args", None) or []
    if isinstance(args, str):
        args = [args]
    parts = [
        getattr(server, "name", "") or "",
        getattr(server, "command", "") or "",
        *(str(arg) for arg in args),
        getattr(server, "url", "") or "",
    ]
    return _tokens(" ".join(parts))


def _matching_phrase(tokens: list[str], phrases: list[tuple[str, ...]]) -> str | None:
    token_set = set(tokens)
    for phrase in phrases:
        if len(phrase) == 1:
            if phrase[0] in token_set:
                return phrase[0]
            continue
        if "".join(phrase) in token_set:
            return " ".join(phrase)
        width = len(phrase)
        for index in range(len(tokens) - width + 1):
            if tuple(tokens[index : index + width]) == phrase:
                return " ".join(phrase)
    return None


def find_tool_source_overlaps(
    mcp_servers: Iterable[Any], connectors: Iterable[Any]
) -> list[ToolSourceOverlap]:
    """Return every (enabled connector, active MCP server) pair that appears to
    reach the same external system.

    Inactive MCP servers and disabled connectors expose no tools, so they never
    overlap. The result is sorted so repeated calls are stable.
    """
    servers = [server for server in mcp_servers if getattr(server, "is_active", True)]
    overlaps: list[ToolSourceOverlap] = []
    for connector in connectors:
        if not getattr(connector, "is_enabled", True):
            continue
        phrases = _identity_phrases(getattr(connector, "kind", ""))
        if not phrases:
            continue
        for server in servers:
            term = _matching_phrase(_server_tokens(server), phrases)
            if term is None:
                continue
            overlaps.append(
                ToolSourceOverlap(
                    kind=connector.kind,
                    connector_id=connector.id,
                    connector_name=connector.name,
                    mcp_server_id=server.id,
                    mcp_server_name=server.name,
                    matched_term=term,
                )
            )
    return sorted(
        overlaps,
        key=lambda item: (
            item.connector_name.lower(),
            item.mcp_server_name.lower(),
            str(item.connector_id),
            str(item.mcp_server_id),
        ),
    )


def _id_keys(raw_ids: Iterable[Any] | None) -> list[str]:
    keys: list[str] = []
    for raw in raw_ids or []:
        try:
            keys.append(str(uuid.UUID(str(raw))))
        except (TypeError, ValueError):
            continue
    return keys


def overlaps_for_service(
    service: Any,
    mcp_servers_by_id: Mapping[str, Any],
    connectors_by_id: Mapping[str, Any],
) -> list[ToolSourceOverlap]:
    """Overlaps among the tool sources one service allowlists.

    ``*_by_id`` map ``str(uuid)`` to the org's MCP server and connector rows.
    Ids on the service that no longer resolve are ignored.
    """
    servers = [
        mcp_servers_by_id[key]
        for key in _id_keys(getattr(service, "mcp_server_ids", None))
        if key in mcp_servers_by_id
    ]
    connectors = [
        connectors_by_id[key]
        for key in _id_keys(getattr(service, "allowed_integration_connector_ids", None))
        if key in connectors_by_id
    ]
    if not servers or not connectors:
        return []
    return find_tool_source_overlaps(servers, connectors)


async def load_tool_source_index(
    db: AsyncSession, org_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the org's MCP servers and connectors keyed by ``str(uuid)``."""
    from backend.db.repos import IntegrationConnectorRepo, MCPServerRepo

    servers = await MCPServerRepo.list_all(db, org_id)
    connectors = await IntegrationConnectorRepo.list_for_org(
        db, org_id, enabled_only=False
    )
    return (
        {str(server.id): server for server in servers},
        {str(connector.id): connector for connector in connectors},
    )


def describe_overlap(overlap: ToolSourceOverlap) -> str:
    """One-line, secret-free description for logs and ``doctor`` output."""
    return (
        f"{overlap.kind} connector '{overlap.connector_name}' and MCP server "
        f"'{overlap.mcp_server_name}' both reach {overlap.kind} "
        f"(matched '{overlap.matched_term}')"
    )
