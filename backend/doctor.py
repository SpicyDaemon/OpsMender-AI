"""`opsmender doctor` — production readiness checks (Sprint 43 P0 #3).

Each check produces a ``CheckResult`` with a status of ``ok``, ``warn``,
or ``fail``. The CLI runs the checks in order, prints a one-line glyph
+ name + detail for each, and exits non-zero when any check failed.

Checks are intentionally pure — no side effects beyond the connections
they explicitly make (DB ping, MCP transport probe, file touch). The
CLI is the only place that prints; the functions return data.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.config_loader import AppConfig, _DEFAULT_JWT_SECRETS


Status = str  # "ok" | "warn" | "fail"


@dataclass
class CheckResult:
    name: str
    status: Status  # "ok" | "warn" | "fail"
    detail: str

    @property
    def glyph(self) -> str:
        # ASCII-safe glyphs so Windows cp1252 consoles don't choke.
        return {"ok": "[ok]", "warn": "[!!]", "fail": "[XX]"}.get(self.status, "[??]")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_jwt_secret(config: AppConfig) -> CheckResult:
    """Verify the JWT secret is not a placeholder and is reasonably long."""
    secret = (config.auth.jwt_secret or "").strip()
    mode = (os.environ.get("OPSMENDER_DEPLOYMENT_MODE") or "").strip().lower()
    is_dev = mode == "development"

    if secret in _DEFAULT_JWT_SECRETS:
        if is_dev:
            return CheckResult(
                "JWT secret",
                "warn",
                "Still using placeholder secret (allowed in development mode).",
            )
        return CheckResult(
            "JWT secret",
            "fail",
            "OPSMENDER_JWT_SECRET is the default placeholder. Generate one "
            "via `openssl rand -hex 32` before deploying.",
        )
    if len(secret) < 32 and not is_dev:
        return CheckResult(
            "JWT secret",
            "warn",
            f"OPSMENDER_JWT_SECRET is only {len(secret)} chars. Recommended ≥ 32.",
        )
    return CheckResult("JWT secret", "ok", f"{len(secret)}-char secret set.")


def check_frontend_static(config: AppConfig) -> CheckResult:
    """The frontend static export must be discoverable for `serve` to mount it."""
    target = pathlib.Path(config.app.frontend_static_dir)
    if not target.exists():
        return CheckResult(
            "Frontend static mount",
            "warn",
            f"{target} does not exist. Run `npm run build` in frontend/ before serving.",
        )
    if not target.is_dir():
        return CheckResult(
            "Frontend static mount", "fail", f"{target} is not a directory."
        )
    index = target / "index.html"
    if not index.exists():
        return CheckResult(
            "Frontend static mount",
            "warn",
            f"{target} exists but is missing index.html.",
        )
    return CheckResult("Frontend static mount", "ok", f"{target} present.")


def check_audit_log(config: AppConfig) -> CheckResult:
    """The audit log path must be writeable so JSONL entries can append."""
    target = pathlib.Path(config.audit.output)
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".doctor-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("Audit log", "fail", f"{parent} not writeable: {exc}")
    return CheckResult("Audit log", "ok", f"{target} writeable.")


async def check_database(factory: async_sessionmaker | None) -> CheckResult:
    """Open a session and run a trivial SELECT to confirm reachability."""
    if factory is None:
        return CheckResult(
            "Database",
            "fail",
            "No DATABASE_URL resolved — cannot connect.",
        )
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return CheckResult("Database", "ok", "SELECT 1 succeeded.")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Database", "fail", f"Connection failed: {exc}")


async def _probe_mcp_server(server_cfg) -> tuple[bool, str]:
    """Open a transient MCP session and list tools to confirm connectivity."""
    from backend.mcp.client import connect, list_tools

    try:
        async with connect(server_cfg) as session:
            tools = await list_tools(session)
            return True, f"{len(tools)} tool(s) reachable."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def check_mcp_servers(factory: async_sessionmaker | None) -> list[CheckResult]:
    """Probe every active MCP server registered in the pool."""
    from backend.mcp.pool import MCPServerPool

    pool = MCPServerPool(factory, env_fallback=[])
    try:
        servers = await pool.list_servers(active_only=True)
    except Exception as exc:  # noqa: BLE001
        return [CheckResult("MCP servers", "fail", f"Pool query failed: {exc}")]

    if not servers:
        return [
            CheckResult(
                "MCP servers",
                "warn",
                "No active MCP servers configured. Add one from Config -> MCP Servers.",
            )
        ]

    results: list[CheckResult] = []
    for server in servers:
        ok, detail = await _probe_mcp_server(server)
        results.append(
            CheckResult(
                f"MCP: {server.name}",
                "ok" if ok else "fail",
                f"({server.transport}) {detail}",
            )
        )
    return results


async def check_paging_chain_age(
    factory: async_sessionmaker | None, *, max_age: timedelta = timedelta(hours=24)
) -> CheckResult:
    """Flag escalation chains stuck running longer than ``max_age``."""
    if factory is None:
        return CheckResult("Paging chains", "fail", "No DB connection.")
    try:
        from backend.db.models import IncidentChainState

        cutoff = datetime.now(timezone.utc) - max_age
        async with factory() as session:
            stmt = select(IncidentChainState).where(
                IncidentChainState.status == "running",
                IncidentChainState.started_at < cutoff,
            )
            stale = (await session.execute(stmt)).scalars().all()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Paging chains", "fail", f"Query failed: {exc}")

    if not stale:
        return CheckResult("Paging chains", "ok", "No long-running chains.")
    return CheckResult(
        "Paging chains",
        "warn",
        f"{len(stale)} chain(s) running for more than {int(max_age.total_seconds() // 3600)}h.",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_all_checks(
    config: AppConfig, factory: async_sessionmaker | None
) -> list[CheckResult]:
    """Run every check in display order."""
    results: list[CheckResult] = [
        check_jwt_secret(config),
        check_frontend_static(config),
        check_audit_log(config),
        await check_database(factory),
    ]
    results.extend(await check_mcp_servers(factory))
    results.append(await check_paging_chain_age(factory))
    return results


def exit_code(results: list[CheckResult]) -> int:
    """0 when every check is ok-or-warn; 1 when any check failed."""
    return 1 if any(r.status == "fail" for r in results) else 0
