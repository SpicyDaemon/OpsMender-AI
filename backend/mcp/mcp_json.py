"""`mcp.json` file mirror — Sprint 42 Step 6.

Two-way sync between the org's ``mcp_servers`` rows and a
Claude-Code-compatible JSON file (default ``~/.opsmender/mcp.json``).

Schema (matches Claude Code's project-scope ``.mcp.json`` byte-for-byte
so the same file works in both tools):

.. code-block:: json

    {
      "mcpServers": {
        "<name>": {
          "type": "stdio" | "http" | "sse",
          "command": "...",  "args": [...],  "env": {...},
          "url": "...",       "headers": {...},
          "opsmender": {
            "is_active": true
          }
        }
      }
    }

Secret-handling rules:

* OAuth tokens live in ``mcp_server_oauth_tokens`` (encrypted via
  Fernet) and **never** land in this file.
* The static bearer ``token`` column is also not written to disk by
  default — operators who want a static bearer should keep it in DB
  via the UI. (Pre-existing tokens in DB are preserved across a
  reconcile because the file simply omits them.)

Conflict semantics:

* **DB→file**: ``export_org`` overwrites the file with the current DB
  state for the org (UI mutations win).
* **File→DB on startup**: ``reconcile_on_startup`` reads the file and
  for each server-by-name applies create-or-update. **Servers present
  only in the DB are not deleted** (additive reconciliation). Use
  ``opsmender mcp reload --prune`` (Step 7) when a destructive sync is
  intended.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.db.models import MCPServer
from backend.db.repos import MCPServerRepo

logger = logging.getLogger(__name__)


def default_path() -> Path:
    """Resolve the mirror path from ``OPSMENDER_MCP_CONFIG_PATH``.

    Falls back to ``~/.opsmender/mcp.json``.
    """
    override = os.environ.get("OPSMENDER_MCP_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".opsmender" / "mcp.json"


def sync_enabled() -> bool:
    """``OPSMENDER_MCP_JSON_SYNC`` env flag (default off).

    Conservative default — multi-tenant deployments must opt in. The
    single-tenant binary path that the locked decisions reference will
    set this to ``true`` in its bundled environment.
    """
    raw = os.environ.get("OPSMENDER_MCP_JSON_SYNC", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def sync_org_id() -> uuid.UUID | None:
    """Optional pinned org UUID from ``OPSMENDER_MCP_JSON_ORG_ID``.

    When unset the syncer mirrors every org's servers serially during
    reconciliation, but route-driven writes mirror only the active org.
    """
    raw = os.environ.get("OPSMENDER_MCP_JSON_ORG_ID", "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        logger.warning(
            "mcp_json: OPSMENDER_MCP_JSON_ORG_ID=%r is not a valid UUID — ignoring",
            raw,
        )
        return None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def server_to_entry(server: MCPServer) -> dict[str, Any]:
    """Serialize one ``MCPServer`` row into the per-server dict shape."""
    entry: dict[str, Any] = {"type": server.transport}
    if server.transport == "stdio":
        if server.command is not None:
            entry["command"] = server.command
        if server.args:
            entry["args"] = list(server.args)
        if server.env_vars:
            entry["env"] = dict(server.env_vars)
    else:
        if server.url is not None:
            entry["url"] = server.url
        # env_vars on http transport may carry static request headers via
        # OPSMENDER_MCP_HEADER_* — round-trip them under "env" to keep the
        # shape regular. (Claude Code uses "headers"; we accept both on
        # read for compatibility.)
        if server.env_vars:
            entry["env"] = dict(server.env_vars)
    if not server.is_active:
        entry["opsmender"] = {"is_active": False}
    return entry


def entry_to_kwargs(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Convert one mcp.json per-server dict to ``MCPServerRepo`` kwargs.

    Tolerates both ``type`` (Claude Code) and ``transport`` (OpsMender).
    HTTP servers may carry a bearer token via either an explicit
    ``headers.Authorization: Bearer <X>`` value or the OpsMender
    ``opsmender.token`` extension; both are extracted into the ``token``
    kwarg so callers can persist them to the DB column.
    """
    transport = entry.get("type") or entry.get("transport") or "stdio"
    opsmender_ext = entry.get("opsmender") or {}

    kwargs: dict[str, Any] = {
        "name": name,
        "transport": transport,
        "command": entry.get("command"),
        "args": list(entry["args"]) if entry.get("args") else None,
        "url": entry.get("url"),
        "env_vars": None,
        "token": None,
        "is_active": bool(opsmender_ext.get("is_active", True)),
    }

    env = entry.get("env")
    if isinstance(env, dict) and env:
        kwargs["env_vars"] = dict(env)

    headers = entry.get("headers")
    if isinstance(headers, dict):
        auth = headers.get("Authorization") or headers.get("authorization")
        if isinstance(auth, str) and auth.lower().startswith("bearer "):
            kwargs["token"] = auth.split(" ", 1)[1].strip() or None

    # Explicit OpsMender extension wins over header-derived token.
    if isinstance(opsmender_ext.get("token"), str):
        kwargs["token"] = opsmender_ext["token"]

    return kwargs


# ---------------------------------------------------------------------------
# File IO
# ---------------------------------------------------------------------------


def read_from_disk(path: Path) -> dict[str, dict[str, Any]]:
    """Return ``{server_name: entry_dict}`` or ``{}`` when file missing.

    Raises ``ValueError`` on malformed JSON or non-dict ``mcpServers``.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"mcp.json at {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"mcp.json at {path} must be a JSON object")
    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(
            f"mcp.json 'mcpServers' must be an object, got {type(servers).__name__}"
        )
    out: dict[str, dict[str, Any]] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise ValueError(f"mcp.json server '{name}' must be a JSON object")
        out[name] = entry
    return out


def write_to_disk(path: Path, servers: Iterable[MCPServer]) -> None:
    """Serialize ``servers`` to ``path``, creating parent dirs as needed."""
    payload: dict[str, Any] = {
        "mcpServers": {srv.name: server_to_entry(srv) for srv in servers}
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Syncer
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    db_only: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MCPJSONSyncer:
    """Lifespan-managed sync helper.

    Routes call ``export_org`` after a successful CRUD commit so the
    file stays in lock-step with DB writes. Startup calls
    ``reconcile_on_startup`` once per known org so file edits made
    while the service was down are applied (file wins on conflict;
    additive — DB-only entries are reported but never deleted).
    """

    def __init__(
        self,
        factory: async_sessionmaker | None,
        *,
        path: Path | None = None,
        enabled: bool | None = None,
        pinned_org_id: uuid.UUID | None = None,
    ) -> None:
        self._factory = factory
        self._path = path or default_path()
        self._enabled = sync_enabled() if enabled is None else enabled
        self._pinned_org_id = (
            pinned_org_id if pinned_org_id is not None else sync_org_id()
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self._factory is not None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def pinned_org_id(self) -> uuid.UUID | None:
        return self._pinned_org_id

    async def export_org(self, org_id: uuid.UUID) -> None:
        """Write the org's current MCP servers to disk (DB→file)."""
        if not self.enabled:
            return
        if self._pinned_org_id is not None and self._pinned_org_id != org_id:
            return
        assert self._factory is not None  # for type checker
        async with self._factory() as session:
            servers = await MCPServerRepo.list_all(session, org_id)
        try:
            write_to_disk(self._path, servers)
            logger.info(
                "mcp_json: exported %d server(s) for org=%s to %s",
                len(servers),
                org_id,
                self._path,
            )
        except OSError as exc:
            logger.warning("mcp_json: export failed (%s): %s", self._path, exc)

    async def reconcile_on_startup(
        self,
        org_id: uuid.UUID,
        *,
        prune: bool = False,
        dry_run: bool = False,
    ) -> ReconcileResult:
        """Apply ``mcp.json`` deltas into the DB for one org.

        File wins on conflict for fields the file actually sets;
        DB-only servers are reported in ``ReconcileResult.db_only``
        and additionally deleted (and recorded in
        ``ReconcileResult.deleted``) when ``prune=True``. When
        ``dry_run=True`` the planned mutations are computed but never
        committed — used by ``opsmender mcp reload`` without
        ``--apply``.
        """
        result = ReconcileResult()
        if not self.enabled:
            return result
        if self._pinned_org_id is not None and self._pinned_org_id != org_id:
            return result
        assert self._factory is not None
        try:
            file_entries = read_from_disk(self._path)
        except ValueError as exc:
            logger.warning("mcp_json: reconcile aborted — %s", exc)
            result.errors.append(str(exc))
            return result

        async with self._factory() as session:
            existing = await MCPServerRepo.list_all(session, org_id)
            existing_by_name = {srv.name: srv for srv in existing}

            for name, entry in file_entries.items():
                try:
                    kwargs = entry_to_kwargs(name, entry)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("mcp_json: server %r invalid — %s", name, exc)
                    result.errors.append(f"{name}: {exc}")
                    continue
                row = existing_by_name.get(name)
                if row is None:
                    await MCPServerRepo.create(session, org_id, **kwargs)
                    result.created.append(name)
                    logger.info(
                        "mcp_json: reconcile created server=%r org=%s", name, org_id
                    )
                else:
                    # Preserve DB-only token when file omits it.
                    if kwargs.get("token") is None:
                        kwargs["token"] = row.token
                    await MCPServerRepo.update(session, org_id, row.id, **kwargs)
                    result.updated.append(name)
                    logger.info(
                        "mcp_json: reconcile updated server=%r org=%s", name, org_id
                    )

            for name, row in existing_by_name.items():
                if name in file_entries:
                    continue
                result.db_only.append(name)
                if prune:
                    await MCPServerRepo.delete(session, org_id, row.id)
                    result.deleted.append(name)
                    logger.info(
                        "mcp_json: reconcile deleted server=%r org=%s (--prune)",
                        name,
                        org_id,
                    )

            if dry_run:
                await session.rollback()
            else:
                await session.commit()

        if result.db_only and not prune:
            logger.info(
                "mcp_json: %d DB-only server(s) not in file (preserved): %s",
                len(result.db_only),
                ", ".join(result.db_only),
            )
        return result
