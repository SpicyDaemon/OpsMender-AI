"""Import MCP servers from a Claude Code config file (Sprint 42 Step 8).

Claude Code stores MCP servers in two places:

* ``~/.claude.json`` — top-level ``projects`` map keyed by absolute
  project path; each project may carry its own ``mcpServers`` block.
* ``.mcp.json`` (or any path the operator points at) — the project-
  scope shape, ``{"mcpServers": {...}}``, which matches OpsMender's
  own file mirror byte-for-byte.

This module discovers either form and produces a normalized
``ImportableServer`` list that the caller (the first-run setup
checklist in Sprint 43, or a future ``opsmender mcp import``
subcommand) can hand to ``MCPServerRepo.create``.

No DB writes happen here — these helpers are pure parsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ImportableServer:
    """One MCP server parsed from an external config file.

    Fields are aligned with ``MCPServerRepo.create`` kwargs so the
    caller can splat the dataclass directly. ``source`` records where
    the entry came from so an ambiguous import (e.g. two projects with
    the same server name in ``~/.claude.json``) can be disambiguated.
    """

    name: str
    transport: str
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env_vars: dict[str, str] | None = None
    token: str | None = None
    source: str = ""  # e.g. "claude:.mcp.json" or "claude:~/.claude.json:/path/to/proj"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def default_user_config_path() -> Path:
    """``~/.claude.json``."""
    return Path.home() / ".claude.json"


def default_project_config_path(cwd: Path | None = None) -> Path:
    """``<cwd>/.mcp.json`` — the project-scope file."""
    return (cwd or Path.cwd()) / ".mcp.json"


def discover() -> list[Path]:
    """Return Claude Code config files that exist on disk.

    Order: user-scope (``~/.claude.json``) first, then project-scope
    (``./.mcp.json``). Callers may want to skip the project-scope file
    when invoked from a directory that isn't a project root.
    """
    found: list[Path] = []
    user = default_user_config_path()
    if user.exists():
        found.append(user)
    proj = default_project_config_path()
    if proj.exists():
        found.append(proj)
    return found


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _entry_to_importable(name: str, entry: dict[str, Any], *, source: str) -> ImportableServer:
    transport = entry.get("type") or "stdio"
    # Claude treats "streamable-http" as an alias for "http".
    if transport == "streamable-http":
        transport = "http"

    args = entry.get("args")
    env = entry.get("env")
    headers = entry.get("headers")

    token: str | None = None
    if isinstance(headers, dict):
        auth = headers.get("Authorization") or headers.get("authorization")
        if isinstance(auth, str) and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip() or None

    return ImportableServer(
        name=name,
        transport=transport,
        command=entry.get("command"),
        args=list(args) if isinstance(args, list) else None,
        url=entry.get("url"),
        env_vars=dict(env) if isinstance(env, dict) and env else None,
        token=token,
        source=source,
    )


def parse(path: Path) -> list[ImportableServer]:
    """Read ``path`` and return a flat list of importable servers.

    Supports both the user-scope shape (``{"projects": {... :
    {"mcpServers": ...}}}``) and the project-scope shape
    (``{"mcpServers": {...}}``). Raises ``ValueError`` on malformed
    JSON or unexpected structure.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object at the top level")

    out: list[ImportableServer] = []

    # Project-scope shape — simple `mcpServers` block at the root.
    direct = raw.get("mcpServers")
    if isinstance(direct, dict):
        for name, entry in direct.items():
            if not isinstance(entry, dict):
                continue
            out.append(
                _entry_to_importable(name, entry, source=f"claude:{path}")
            )

    # User-scope shape — `projects[<path>].mcpServers`.
    projects = raw.get("projects")
    if isinstance(projects, dict):
        for project_path, project_cfg in projects.items():
            if not isinstance(project_cfg, dict):
                continue
            servers = project_cfg.get("mcpServers")
            if not isinstance(servers, dict):
                continue
            for name, entry in servers.items():
                if not isinstance(entry, dict):
                    continue
                out.append(
                    _entry_to_importable(
                        name,
                        entry,
                        source=f"claude:{path}:{project_path}",
                    )
                )

    return out
