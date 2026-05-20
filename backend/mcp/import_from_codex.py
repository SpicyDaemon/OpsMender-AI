"""Import MCP servers from a Codex config file (Sprint 42 Step 8).

Codex stores MCP servers in TOML at ``~/.codex/config.toml`` (user
scope) or ``.codex/config.toml`` (project scope, trusted projects
only). Per-server entries live under ``[mcp_servers.<name>]``:

.. code-block:: toml

    [mcp_servers.kube-prod]
    command = "npx"
    args = ["-y", "@anthropic/mcp-server-k8s"]
    env_vars = ["KUBECONFIG"]

    [mcp_servers.kube-prod.env]
    KUBECONFIG = "/etc/kube/config"

    [mcp_servers.sentry]
    url = "https://mcp.sentry.dev/mcp"
    bearer_token_env_var = "SENTRY_TOKEN"

This module produces normalized ``ImportableServer`` rows (the same
dataclass returned by ``import_from_claude``) so a future
``opsmender mcp import`` subcommand can hand them off to
``MCPServerRepo.create``.

The Codex-specific knobs that don't map onto OpsMender's schema —
``startup_timeout_sec``, ``tool_timeout_sec``, ``enabled_tools``,
``default_tools_approval_mode`` — are not propagated; callers can
read them from the source dict if they want to surface a warning.

Pure parsing only. No DB writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from backend.mcp.import_from_claude import ImportableServer


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def default_user_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def default_project_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".codex" / "config.toml"


def discover() -> list[Path]:
    """Return Codex config files that exist on disk."""
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


def _table_to_importable(
    name: str, table: dict[str, Any], *, source: str
) -> ImportableServer | None:
    """Convert one ``[mcp_servers.<name>]`` table to an ImportableServer."""
    if table.get("enabled") is False:
        return None

    is_http = "url" in table
    transport = "http" if is_http else "stdio"

    command = table.get("command") if not is_http else None
    args = table.get("args") if isinstance(table.get("args"), list) else None
    url = table.get("url") if is_http else None

    # Stdio env: nested `[mcp_servers.<name>.env]` carries inline
    # values; `env_vars` is a forwarding list referencing the process
    # environment. Capture inline values directly; forwarding entries
    # cannot be resolved without the runtime env, so they are skipped.
    env_vars: dict[str, str] | None = None
    env_table = table.get("env")
    if isinstance(env_table, dict) and env_table:
        env_vars = {k: str(v) for k, v in env_table.items() if isinstance(k, str)}

    # HTTP headers: inline static ones in `http_headers`; env-driven
    # ones in `env_http_headers` reference process env and are skipped
    # for the same reason as `env_vars` on stdio.
    if is_http:
        http_headers = table.get("http_headers")
        if isinstance(http_headers, dict) and http_headers:
            env_vars = dict(env_vars or {})
            for k, v in http_headers.items():
                if isinstance(k, str):
                    env_vars[k] = str(v)

    # Bearer token: Codex stores the *env var name* that holds the
    # token (`bearer_token_env_var`), not the token itself. Surface
    # this as the env_vars dict — operators can copy the actual value
    # in afterward — and leave the OpsMender `token` column empty so
    # nothing leaks.
    bearer_env = table.get("bearer_token_env_var")
    if isinstance(bearer_env, str) and bearer_env:
        env_vars = dict(env_vars or {})
        env_vars.setdefault("OPSMENDER_BEARER_TOKEN_ENV_VAR", bearer_env)

    return ImportableServer(
        name=name,
        transport=transport,
        command=command,
        args=list(args) if args else None,
        url=url,
        env_vars=env_vars,
        token=None,
        source=f"codex:{source}",
    )


def parse(path: Path) -> list[ImportableServer]:
    """Read ``path`` and return a flat list of importable servers.

    Raises ``ValueError`` on malformed TOML.
    """
    if not path.exists():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML: {exc}") from exc

    servers = raw.get("mcp_servers")
    if not isinstance(servers, dict):
        return []

    out: list[ImportableServer] = []
    for name, table in servers.items():
        if not isinstance(table, dict):
            continue
        item = _table_to_importable(name, table, source=str(path))
        if item is not None:
            out.append(item)
    return out
