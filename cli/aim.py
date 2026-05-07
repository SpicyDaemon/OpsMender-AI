"""Entry point for the ``aim`` command.

Supports subcommands and global options.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import pathlib
import sys

import contextlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.audit.logger import AuditEntry, AuditLogger
from backend.approvals import ApprovalService
from backend.config_loader import Config
from backend.db.engine import get_engine, get_session_factory, resolve_database_url
from backend.db.models import Organization
from backend.db.repos import ApprovalRequestRepo, ModelConfigRepo, SessionRepo
from backend.llm import ProviderRegistry
from backend.mcp.client import MCPClientError, connect, list_tools
from backend.mcp.pool import MCPServerPool
from backend.tiers.sandbox import build_sandbox_for_session
from backend.workflow.rollback import (
    reconstruct_tool_calls,
    replay_compensating_inverses,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aim", description="AI Incident Manager CLI")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to environment file (default: .env)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="Validate config and MCP server connectivity")

    # -- serve --------------------------------------------------------------
    serve_parser = sub.add_parser(
        "serve",
        help="Start the HTTP + WebSocket API (and embedded frontend)",
    )
    serve_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind host (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (dev only; ignored in a frozen binary)",
    )
    serve_parser.add_argument(
        "--skip-migrations",
        action="store_true",
        help="Don't run `alembic upgrade head` before starting the server",
    )

    # -- run ----------------------------------------------------------------
    run_parser = sub.add_parser("run", help="Run an incident response session")
    run_parser.add_argument(
        "--incident",
        required=True,
        help="Incident description (what happened)",
    )
    run_parser.add_argument(
        "--tier",
        type=int,
        default=None,
        help="Override tier level (0-3). Defaults to config value.",
    )
    run_parser.add_argument(
        "--skill-file",
        default="examples/SKILL.md",
        help="Path to SKILL.md file (default: examples/SKILL.md)",
    )
    run_parser.add_argument(
        "--model",
        default=None,
        help="Anthropic model ID (default: claude-sonnet-4-20250514)",
    )
    run_parser.add_argument(
        "--mcp-server",
        default=None,
        help="Name of the MCP server to use (default: first active server in DB/env)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run with stub LLM and no MCP (offline mode)",
    )
    run_parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Write final state to a JSON file",
    )

    # -- config -------------------------------------------------------------
    config_parser = sub.add_parser("config", help="View or validate configuration")
    config_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output configuration as JSON",
    )
    config_parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate config and skill file, report errors",
    )
    config_parser.add_argument(
        "--skill-file",
        default=None,
        help="Skill file to validate (used with --validate)",
    )
    config_sub = config_parser.add_subparsers(dest="config_command")

    model_parser = config_sub.add_parser(
        "model",
        help="Discover providers or set the default model configuration",
    )
    model_sub = model_parser.add_subparsers(dest="model_command")

    model_list = model_sub.add_parser("list", help="List available provider models")
    model_list.add_argument(
        "--provider",
        choices=["anthropic", "openai", "azure_openai", "ollama"],
        default=None,
        help="Filter to one provider",
    )
    model_list.add_argument(
        "--model-id",
        default=None,
        help="Override the probe model ID",
    )
    model_list.add_argument(
        "--api-key-env-var",
        default=None,
        help="Override the API key environment variable used for discovery",
    )
    model_list.add_argument(
        "--base-url",
        default=None,
        help="Override the provider base URL",
    )
    model_list.add_argument(
        "--api-version",
        default=None,
        help="Override the provider API version",
    )
    model_list.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output provider discovery results as JSON",
    )

    model_set = model_sub.add_parser(
        "set",
        help="Validate and save the default model configuration",
    )
    model_set.add_argument(
        "--name",
        default=None,
        help="Config name to create or update (default: provider:model_id)",
    )
    model_set.add_argument(
        "--provider",
        required=True,
        choices=["anthropic", "openai", "azure_openai", "ollama"],
        help="LLM provider to configure",
    )
    model_set.add_argument(
        "--model-id",
        required=True,
        help="Model identifier or deployment name",
    )
    model_set.add_argument(
        "--api-key-env-var",
        default=None,
        help="Environment variable containing the provider API key",
    )
    model_set.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL or local runtime endpoint",
    )
    model_set.add_argument(
        "--api-version",
        default=None,
        help="Azure/OpenAI API version override",
    )
    model_set.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Default max_tokens value to persist",
    )
    model_set.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Default temperature value to persist",
    )
    model_set.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output the saved model config as JSON",
    )

    model_bootstrap = model_sub.add_parser(
        "bootstrap",
        help="Bootstrap the first default model config with prompts or flags",
    )
    model_bootstrap.add_argument(
        "--name",
        default=None,
        help="Config name to create or update (default: provider:model_id)",
    )
    model_bootstrap.add_argument(
        "--provider",
        choices=["anthropic", "openai", "azure_openai", "ollama"],
        default=None,
        help="LLM provider to configure",
    )
    model_bootstrap.add_argument(
        "--model-id",
        default=None,
        help="Model identifier or deployment name",
    )
    model_bootstrap.add_argument(
        "--api-key-env-var",
        default=None,
        help="Environment variable containing the provider API key",
    )
    model_bootstrap.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL or local runtime endpoint",
    )
    model_bootstrap.add_argument(
        "--api-version",
        default=None,
        help="Azure/OpenAI API version override",
    )
    model_bootstrap.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Default max_tokens value to persist",
    )
    model_bootstrap.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Default temperature value to persist",
    )
    model_bootstrap.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output the saved model config as JSON",
    )

    # -- audit --------------------------------------------------------------
    audit_parser = sub.add_parser("audit", help="View the audit log")
    audit_parser.add_argument(
        "--last",
        type=int,
        metavar="N",
        default=None,
        help="Show the last N entries (default: show all)",
    )
    audit_parser.add_argument(
        "--session",
        type=str,
        metavar="ID",
        default=None,
        help="Filter entries by session ID",
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output entries as JSON lines instead of a table",
    )

    # -- approvals ----------------------------------------------------------
    approvals_parser = sub.add_parser(
        "approvals",
        help="List or resolve Tier 1 approval requests",
    )
    approvals_sub = approvals_parser.add_subparsers(dest="approvals_command")

    approvals_list = approvals_sub.add_parser("list", help="List approval requests")
    approvals_list.add_argument(
        "--status",
        default=None,
        choices=["pending", "approved", "rejected", "expired"],
        help="Filter by approval status",
    )
    approvals_list.add_argument(
        "--session",
        type=str,
        metavar="ID",
        default=None,
        help="Filter by session ID",
    )
    approvals_list.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output approval requests as JSON",
    )

    approvals_approve = approvals_sub.add_parser(
        "approve", help="Approve a pending request"
    )
    approvals_approve.add_argument("request_id", help="Approval request ID")
    approvals_approve.add_argument(
        "--user-id",
        default=None,
        help="Resolver user ID to record on the approval",
    )
    approvals_approve.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output the resolved request as JSON",
    )

    approvals_reject = approvals_sub.add_parser(
        "reject", help="Reject a pending request"
    )
    approvals_reject.add_argument("request_id", help="Approval request ID")
    approvals_reject.add_argument(
        "--user-id",
        default=None,
        help="Resolver user ID to record on the approval",
    )
    approvals_reject.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output the resolved request as JSON",
    )

    # -- saml ---------------------------------------------------------------
    saml_parser = sub.add_parser(
        "saml",
        help="SAML SP utilities (Sprint 30)",
    )
    saml_sub = saml_parser.add_subparsers(dest="saml_command")
    saml_keys = saml_sub.add_parser(
        "gen-sp-keys",
        help="Generate a self-signed SP signing keypair (PEM, stdout)",
    )
    saml_keys.add_argument(
        "--cn",
        default="aim-sp",
        help="Subject Common Name on the cert (default: aim-sp)",
    )
    saml_keys.add_argument(
        "--days",
        type=int,
        default=3650,
        help="Cert validity in days (default: 3650 = 10y)",
    )

    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


# -- check subcommand --------------------------------------------------------


async def _check_server(server_cfg, timeout: float = 10.0) -> tuple[str, bool, str]:
    """Try to connect to one MCP server and list its tools.

    Returns (server_name, success, detail_message).
    """
    try:
        async with connect(server_cfg) as session:
            tools = await list_tools(session)
            tool_names = [t.name for t in tools]
            return (
                server_cfg.name,
                True,
                f"{len(tools)} tools: {', '.join(tool_names) if tool_names else '(none)'}",
            )
    except Exception as exc:
        return server_cfg.name, False, str(exc)


async def _run_check(cfg: Config) -> int:
    """Validate config and test connectivity to all configured MCP servers.

    Servers are resolved through :class:`MCPServerPool` so newly added DB
    entries appear without a restart.  Env-defined servers act as fallback
    only when the database is unreachable.
    """
    session_factory = None
    engine = None
    try:
        engine = get_engine(_database_url(cfg))
        session_factory = get_session_factory(engine)
    except Exception:
        session_factory = None

    pool = MCPServerPool(session_factory, env_fallback=cfg.mcp_servers)
    try:
        servers = await pool.list_servers(active_only=True)
        print(f"Config OK — {len(servers)} MCP server(s) available\n")

        if not servers:
            print(
                "No MCP servers configured. Add one from the dashboard "
                "(/dashboard/config) or seed AIM_MCP_SERVERS_JSON in your .env "
                "as a fallback."
            )
            return 0

        all_ok = True
        for server in servers:
            name, ok, detail = await _check_server(server)
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {name} ({server.transport}) — {detail}")
            if not ok:
                all_ok = False

        print()
        if all_ok:
            print("All servers reachable.")
        else:
            print("Some servers failed. Check config and server availability.")
        return 0 if all_ok else 1
    finally:
        if engine is not None:
            await engine.dispose()


# -- audit subcommand --------------------------------------------------------


def _format_entry(entry: AuditEntry) -> str:
    """Format a single audit entry as a human-readable line."""
    ts = entry.timestamp[:19].replace("T", " ")  # trim to seconds
    status = "PASS" if entry.permitted else "FAIL"
    etype = entry.entry_type.value
    tool = entry.tool_name or "-"
    duration = f"{entry.duration_ms}ms" if entry.duration_ms is not None else "-"
    reason = ""
    if entry.block_reason:
        reason = f"  reason={entry.block_reason}"
    return f"{ts}  [{status}] {etype:<20s}  tier={entry.tier}  tool={tool}  dur={duration}  session={entry.session_id[:12]}{reason}"


def _run_audit(cfg: Config, args: argparse.Namespace) -> int:
    """Display audit log entries."""
    log_path = pathlib.Path(cfg.audit.output)
    if not log_path.is_file():
        print(f"Audit log not found: {log_path}")
        print("No audit entries recorded yet.")
        return 0

    logger = AuditLogger(log_path)

    # Apply filters
    if args.session:
        entries = logger.read_by_session(args.session)
    elif args.last:
        entries = logger.read_last(args.last)
    else:
        entries = logger.read_all()

    if not entries:
        print("No audit entries found.")
        return 0

    if args.json_output:
        for entry in entries:
            print(json.dumps(entry.to_dict(), default=str))
    else:
        print(f"Audit log: {log_path} ({len(entries)} entries)\n")
        for entry in entries:
            print(_format_entry(entry))
        print()

    return 0


def _database_url(cfg: Config) -> str:
    return resolve_database_url(cfg.db)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _approval_to_dict(request) -> dict[str, object]:
    return {
        "id": str(request.id),
        "session_id": str(request.session_id),
        "action": request.action,
        "justification": request.justification,
        "status": request.status,
        "requested_at": request.requested_at.isoformat(),
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
        "resolved_by": str(request.resolved_by) if request.resolved_by else None,
        "expires_at": _as_utc(request.expires_at).isoformat(),
    }


def _format_approval(request) -> str:
    expires = _as_utc(request.expires_at).isoformat(timespec="seconds")
    return (
        f"{str(request.id)[:12]}  status={request.status:<8s}  "
        f"session={str(request.session_id)[:12]}  expires={expires}  "
        f"tool={request.action.get('tool_name', '?')}"
    )


def _model_config_to_dict(config) -> dict[str, object]:
    return {
        "id": str(config.id),
        "name": config.name,
        "provider": config.provider,
        "model_id": config.model_id,
        "api_key_env_var": config.api_key_env_var,
        "base_url": config.base_url,
        "api_version": config.api_version,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "is_default": config.is_default,
        "created_at": config.created_at.isoformat(),
    }


def _format_provider_models(item: dict[str, object]) -> str:
    models = item.get("models") or []
    model_text = ", ".join(str(model) for model in models[:8]) if models else "(none)"
    if len(models) > 8:
        model_text = f"{model_text}, ..."
    status = "available" if item.get("available") else "unavailable"
    detail = model_text if item.get("available") else item.get("error") or "unknown error"
    return f"{item['provider']:<13s} {status:<11s} {detail}"


def _format_model_config(config) -> str:
    default = " default" if config.is_default else ""
    return (
        f"{config.name}  provider={config.provider}  model={config.model_id}"
        f"{default}"
    )


def _warnings_to_dict(warnings) -> list[dict[str, str]]:
    return [
        {"code": warning.code, "message": warning.message}
        for warning in warnings
    ]


def _print_model_validation_warnings(warnings) -> None:
    for warning in warnings:
        print(f"Warning: {warning.message}", file=sys.stderr)


def _prompt_value(prompt: str, *, default: str | None = None) -> str:
    suffix = "" if default in (None, "") else f" [{default}]"
    raw = input(f"{prompt}{suffix}: ").strip()
    if raw:
        return raw
    return default or ""


def _bootstrap_model_args(
    args: argparse.Namespace,
    registry: ProviderRegistry,
) -> argparse.Namespace:
    provider = args.provider or _prompt_value(
        "Provider (anthropic/openai/azure_openai/ollama)",
        default="openai",
    )
    spec = registry.get_spec(provider)

    model_id = args.model_id or _prompt_value(
        "Model ID or deployment name",
        default=spec.default_model_id,
    )
    api_key_env_var = args.api_key_env_var
    if spec.requires_api_key:
        api_key_env_var = api_key_env_var or _prompt_value(
            "API key env var reference",
            default=spec.default_api_key_env_var,
        )
    base_url = args.base_url
    if spec.requires_base_url:
        base_url = base_url or _prompt_value("Base URL")
    api_version = args.api_version
    if spec.requires_api_version:
        api_version = api_version or _prompt_value("API version")

    args.provider = provider
    args.model_id = model_id
    args.api_key_env_var = api_key_env_var or None
    args.base_url = base_url or None
    args.api_version = api_version or None
    args.name = args.name or f"{provider}:{model_id}"
    return args


async def _resolve_cli_org(db) -> uuid.UUID:
    result = await db.execute(select(Organization).order_by(Organization.created_at).limit(1))
    org = result.scalar_one_or_none()
    if org:
        return org.id
    return uuid.UUID("00000000-0000-0000-0000-000000000000")


async def _persist_model_config(cfg: Config, args: argparse.Namespace):
    registry = ProviderRegistry()
    validation = registry.validate_model_config(
        provider=args.provider,
        model_id=args.model_id,
        api_key_env_var=args.api_key_env_var,
        base_url=args.base_url,
        api_version=args.api_version,
        allow_unverified=True,
    )

    engine = get_engine(_database_url(cfg))
    factory = get_session_factory(engine)
    try:
        async with factory() as db:
            name = args.name or f"{args.provider}:{args.model_id}"
            org_id = await _resolve_cli_org(db)
            saved = await ModelConfigRepo.upsert(
                db,
                org_id,
                name=name,
                provider=args.provider,
                model_id=args.model_id,
                api_key_env_var=args.api_key_env_var,
                base_url=args.base_url,
                api_version=args.api_version,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
            await ModelConfigRepo.set_default(db, org_id, saved.id)
            await db.commit()
            refreshed = await ModelConfigRepo.get_by_id(db, org_id, saved.id)
            if refreshed is None:
                raise RuntimeError("Saved model config could not be reloaded.")
            await db.refresh(refreshed)
            return refreshed, validation.warnings
    finally:
        await engine.dispose()


async def _run_approvals(cfg: Config, args: argparse.Namespace) -> int:
    if not args.approvals_command:
        print("Usage: aim approvals {list,approve,reject} ...", file=sys.stderr)
        return 1

    engine = get_engine(_database_url(cfg))
    factory = get_session_factory(engine)

    try:
        async with factory() as db:
            if args.approvals_command == "list":
                session_id = uuid.UUID(args.session) if args.session else None
                org_id = await _resolve_cli_org(db)
                items = await ApprovalRequestRepo.list(
                    db,
                    org_id,
                    status=args.status,
                    session_id=session_id,
                )
                if args.json_output:
                    print(json.dumps([_approval_to_dict(item) for item in items], indent=2))
                elif not items:
                    print("No approval requests found.")
                else:
                    for item in items:
                        print(_format_approval(item))
                return 0

            request_id = uuid.UUID(args.request_id)
            resolved_by = uuid.UUID(args.user_id) if args.user_id else None
            decision = (
                "approved" if args.approvals_command == "approve" else "rejected"
            )
            org_id = await _resolve_cli_org(db)
            request = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
            if request is None:
                print(f"Approval request not found: {request_id}", file=sys.stderr)
                return 1
            if datetime.now(timezone.utc) >= _as_utc(request.expires_at):
                await ApprovalRequestRepo.resolve(db, org_id, request_id, status="expired")
                await SessionRepo.set_status(
                    db,
                    org_id,
                    request.session_id,
                    status="timed_out",
                    ended_at=datetime.now(timezone.utc),
                )
                await db.commit()
                print(
                    f"Approval request expired before it could be resolved: {request_id}",
                    file=sys.stderr,
                )
                return 1

            updated = await ApprovalRequestRepo.resolve(
                db,
                org_id,
                request_id,
                status=decision,
                resolved_by=resolved_by,
            )
            if not updated:
                request = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
                if request is None:
                    print(f"Approval request not found: {request_id}", file=sys.stderr)
                    return 1
                print(
                    f"Approval request is already {request.status}: {request_id}",
                    file=sys.stderr,
                )
                return 1

            request = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
            if request is None:
                print(f"Approval request not found: {request_id}", file=sys.stderr)
                return 1

            if request.status == "expired":
                await SessionRepo.set_status(
                    db,
                    org_id,
                    request.session_id,
                    status="timed_out",
                    ended_at=datetime.now(timezone.utc),
                )
            else:
                await SessionRepo.set_status(db, org_id, request.session_id, status="active")
            await db.commit()
            await db.refresh(request)

            if args.json_output:
                print(json.dumps(_approval_to_dict(request), indent=2))
            else:
                print(_format_approval(request))
            return 0
    except ValueError as exc:
        print(f"Invalid UUID: {exc}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError) as exc:
        print(
            "Approval command failed: database unavailable. "
            "Set AIM_DATABASE_URL or use the local DB fallback for approval commands. "
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Approval command failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


# -- config subcommand -------------------------------------------------------


def _run_config(cfg: Config, args: argparse.Namespace) -> int:
    """Display or validate the current configuration."""
    if getattr(args, "config_command", None) == "model":
        return asyncio.run(_run_config_model(cfg, args))

    if args.json_output:
        import dataclasses as _dc

        def _to_dict(obj):
            if _dc.is_dataclass(obj) and not isinstance(obj, type):
                return {k: _to_dict(v) for k, v in _dc.asdict(obj).items()}
            if isinstance(obj, list):
                return [_to_dict(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _to_dict(v) for k, v in obj.items()}
            return obj

        print(json.dumps(_to_dict(cfg), indent=2))
        return 0

    if args.validate:
        return _validate_config(cfg, args)

    # Default: human-readable summary
    print(f"Env file:     {args.config or '.env'}")
    print(f"Tier:        {cfg.tiers.get('default', '(not set)')}")
    print(f"Log level:   {cfg.logging.get('level', '(not set)')}")
    print(f"Audit log:   {cfg.audit.output}")
    print(f"MCP servers: {len(cfg.mcp_servers)}")
    for s in cfg.mcp_servers:
        detail = s.command if s.transport == "stdio" else s.url
        print(f"  - {s.name} ({s.transport}) -> {detail}")
    if not cfg.mcp_servers:
        print("  (none configured)")
    return 0


def _validate_config(cfg: Config, args: argparse.Namespace) -> int:
    """Validate config and optionally a skill file. Return 0 if valid."""
    errors: list[str] = []

    # Tier validation
    default_tier = cfg.tiers.get("default")
    if default_tier not in (0, 1, 2, 3):
        errors.append(f"tiers.default must be 0-3, got {default_tier}")

    # Audit path writable check
    audit_path = pathlib.Path(cfg.audit.output)
    audit_dir = audit_path.parent
    if audit_dir.exists() and not os.access(audit_dir, os.W_OK):
        errors.append(f"Audit directory not writable: {audit_dir}")

    # MCP server configs
    for s in cfg.mcp_servers:
        if s.transport == "stdio" and not s.command:
            errors.append(f"MCP server '{s.name}': stdio requires 'command'")
        if s.transport in ("sse", "http") and not s.url:
            errors.append(f"MCP server '{s.name}': {s.transport} requires 'url'")

    # Skill file validation (optional)
    skill_path = args.skill_file
    if skill_path:
        from backend.skills.parser import load as load_skill_def

        p = pathlib.Path(skill_path)
        if not p.is_file():
            errors.append(f"Skill file not found: {p}")
        else:
            try:
                skill_def = load_skill_def(p)
                print(f"Skill file:  {p} ({len(skill_def.operations)} operations)")
            except Exception as exc:
                errors.append(f"Skill file parse error: {exc}")

    if errors:
        print("Validation FAILED:\n")
        for err in errors:
            print(f"  x {err}")
        return 1

    print("Validation OK — configuration is valid.")
    return 0


async def _run_config_model(cfg: Config, args: argparse.Namespace) -> int:
    if not args.model_command:
        print("Usage: aim config model {list,set,bootstrap} ...", file=sys.stderr)
        return 1

    registry = ProviderRegistry()

    if args.model_command == "list":
        try:
            items = registry.discover_models(
                provider=args.provider,
                model_id=args.model_id,
                api_key_env_var=args.api_key_env_var,
                base_url=args.base_url,
                api_version=args.api_version,
            )
        except ValueError as exc:
            print(f"Model discovery failed: {exc}", file=sys.stderr)
            return 1

        if args.json_output:
            print(json.dumps({"items": items, "total": len(items)}, indent=2))
        elif not items:
            print("No providers found.")
        else:
            for item in items:
                print(_format_provider_models(item))
        return 0

    try:
        if args.model_command == "bootstrap":
            args = _bootstrap_model_args(args, registry)
        saved, warnings = await _persist_model_config(cfg, args)
    except ValueError as exc:
        print(f"Model config validation failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError, RuntimeError) as exc:
        print(
            "Model config command failed: database unavailable. "
            "Set AIM_DATABASE_URL or use the local DB fallback for model config commands. "
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json_output:
        print(
            json.dumps(
                {
                    "config": _model_config_to_dict(saved),
                    "warnings": _warnings_to_dict(warnings),
                },
                indent=2,
            )
        )
    else:
        print(_format_model_config(saved))
        _print_model_validation_warnings(warnings)
    return 0


# -- run subcommand ----------------------------------------------------------


async def _run_incident(cfg: Config, args: argparse.Namespace) -> int:
    """Execute a full incident response session."""
    from backend.agent.graph import build_graph
    from backend.agent.llm import AnthropicLLM, StubLLM
    from backend.skills.parser import load as load_skill_def

    # 1. Load skill definition
    skill_path = pathlib.Path(args.skill_file)
    if not skill_path.is_file():
        print(f"Skill file not found: {skill_path}", file=sys.stderr)
        return 1
    skill_def = load_skill_def(skill_path)

    # 2. Determine tier
    tier = args.tier if args.tier is not None else cfg.tiers.get("default", 2)
    if tier not in (0, 1, 2, 3):
        print(f"Invalid tier: {tier}. Must be 0-3.", file=sys.stderr)
        return 1

    # 3. Session setup
    session_id = str(uuid.uuid4())
    audit_logger = AuditLogger(cfg.audit.output)
    db_engine = None
    session_factory = None
    approval_service = None

    # The MCP pool needs a session factory.  Create one eagerly so the pool
    # can resolve DB-backed servers even when tier != 1.
    try:
        db_engine = get_engine(_database_url(cfg))
        session_factory = get_session_factory(db_engine)
    except Exception:
        db_engine = None
        session_factory = None

    if tier == 1:
        if session_factory is None:
            print(
                "Tier 1 approval flow requires a reachable database.",
                file=sys.stderr,
            )
            return 1
        try:
            async with session_factory() as db:
                db_session = await SessionRepo.create(
                    db,
                    tier=tier,
                    model_provider="stub" if args.dry_run else "anthropic",
                    model_id=args.model or "claude-sonnet-4-20250514",
                )
                await db.commit()
                await db.refresh(db_session)
                session_id = str(db_session.id)
            approval_service = ApprovalService(
                session_factory,
                timeout_seconds=cfg.approvals.timeout_seconds,
            )
        except Exception as exc:
            if db_engine is not None:
                await db_engine.dispose()
            print(
                "Tier 1 approval flow requires a reachable database. "
                f"Connection/setup failed: {exc}",
                file=sys.stderr,
            )
            return 1

    # 4. Create LLM
    if args.dry_run:
        llm = StubLLM()
        print("Dry-run mode: using stub LLM (no API calls)")
    else:
        model_kwargs = {}
        if args.model:
            model_kwargs["model"] = args.model
        try:
            llm = AnthropicLLM(**model_kwargs)
        except (ImportError, EnvironmentError) as exc:
            print(f"LLM setup failed: {exc}", file=sys.stderr)
            return 1

    print(f"Session:  {session_id}")
    print(f"Tier:     {tier}")
    print(f"Skill:    {skill_path}")
    print(f"Incident: {args.incident}")
    print()

    # 5. Connect to MCP server(s) if available.  We resolve through the
    # MCPServerPool so DB-backed servers and `.env` fallbacks both work.
    mcp_session = None
    tier0_sandbox = None
    tier0_plan_tool_names: list[str] | None = None
    mcp_ctx = contextlib.AsyncExitStack()

    try:
        await mcp_ctx.__aenter__()

        if not args.dry_run:
            pool = MCPServerPool(session_factory, env_fallback=cfg.mcp_servers)
            if args.mcp_server:
                server = await pool.get_server(args.mcp_server)
                if server is None:
                    print(
                        f"MCP server '{args.mcp_server}' not found in DB or env.",
                        file=sys.stderr,
                    )
                    return 1
            else:
                servers = await pool.list_servers(active_only=True)
                server = servers[0] if servers else None

            if server is not None:
                print(f"Connecting to MCP server: {server.name} ({server.transport})...")
                try:
                    mcp_session = await mcp_ctx.enter_async_context(connect(server))
                    print(f"Connected to {server.name}.\n")
                    if tier == 0:
                        tier0_sandbox = await build_sandbox_for_session(
                            mcp_session, skill_def
                        )
                        tier0_plan_tool_names = sorted(
                            tier0_sandbox.allowed_tool_names
                        )
                        print(
                            "Tier 0 sandbox allowlist: "
                            f"{len(tier0_plan_tool_names)} tool(s) visible to the workflow."
                        )
                        if tier0_plan_tool_names:
                            print(", ".join(tier0_plan_tool_names))
                        else:
                            print("(no rollback-safe tools available)")
                        print()
                except Exception as exc:
                    print(f"MCP connection failed: {exc}", file=sys.stderr)
                    print("Continuing without MCP (no tool execution).\n")
                    mcp_session = None

        # 6. Log session start
        audit_logger.log_session_start(session_id, tier)

        # 7. Build and invoke the workflow graph.
        # Tier 0 sessions get hard per-node + session wall clocks — see
        # backend/agent/timeouts.py.
        from backend.agent.timeouts import (
            Tier0TimeConfig,
            ainvoke_with_session_timeout,
        )

        tier0_time_config: Tier0TimeConfig | None = None
        if tier == 0:
            tier0_time_config = Tier0TimeConfig(
                max_session_seconds=cfg.tier0.max_session_seconds,
                max_node_seconds=cfg.tier0.max_node_seconds,
            )
            print(
                f"Tier 0 time limits: {tier0_time_config.max_session_seconds}s session, "
                f"{tier0_time_config.max_node_seconds}s per node\n"
            )

        graph = build_graph(
            tier=tier,
            skill_def=skill_def,
            llm=llm,
            mcp_session=mcp_session,
            audit_logger=audit_logger if mcp_session else None,
            approval_service=approval_service,
            tier0_time_config=tier0_time_config,
            plan_tool_names=tier0_plan_tool_names,
            tool_caller=tier0_sandbox.call_tool if tier0_sandbox is not None else None,
        )

        print("Running workflow: observe -> diagnose -> plan -> tier_gate -> execute -> verify -> summarize\n")

        initial_state = {
            "session_id": session_id,
            "tier": tier,
            "incident_description": args.incident,
            "skill_definition_path": str(skill_path),
        }
        if tier0_time_config is not None:
            result = await ainvoke_with_session_timeout(
                graph,
                initial_state,
                seconds=tier0_time_config.max_session_seconds,
            )
        else:
            result = await graph.ainvoke(initial_state)

        # 8. Tier 0 auto-rollback on failure / timeout.
        if (
            tier == 0
            and mcp_session is not None
            and tier0_sandbox is not None
            and result.get("status") in {"failed", "timed_out"}
        ):
            rollback_candidates = reconstruct_tool_calls(
                audit_logger.read_by_session(session_id)
            )
            if rollback_candidates:
                rollback_report = await replay_compensating_inverses(
                    session_id=session_id,
                    tier=tier,
                    tool_calls=rollback_candidates,
                    skill_def=skill_def,
                    caller=lambda tool_name, params: tier0_sandbox.call_tool(
                        mcp_session, tool_name, params
                    ),
                    audit_logger=audit_logger,
                )
                result["rollback"] = {
                    "attempted": rollback_report.attempted,
                    "succeeded": rollback_report.succeeded,
                    "failed": rollback_report.failed,
                    "skipped": rollback_report.skipped,
                    "steps": [
                        {
                            "original_tool": step.original_tool,
                            "inverse_tool": step.inverse_tool,
                            "parameters": step.parameters,
                            "status": step.status,
                            "error": step.error,
                        }
                        for step in rollback_report.steps
                    ],
                }
                print(
                    "Tier 0 auto-rollback: "
                    f"{rollback_report.succeeded} succeeded, "
                    f"{rollback_report.failed} failed, "
                    f"{rollback_report.skipped} skipped.\n"
                )

        # 9. Log session end
        audit_logger.log_session_end(session_id, tier)
        if session_factory is not None:
            try:
                async with session_factory() as db:
                    final_status = result.get("status", "completed")
                    if final_status == "timed_out":
                        await SessionRepo.set_status(
                            db,
                            uuid.UUID(session_id),
                            status="timed_out",
                            summary=result.get("summary"),
                            ended_at=datetime.now(timezone.utc),
                        )
                    else:
                        await SessionRepo.end_session(
                            db,
                            uuid.UUID(session_id),
                            status=final_status,
                            summary=result.get("summary"),
                        )
                    await db.commit()
            except Exception:
                pass  # DB may not have tables (e.g. local SQLite without migrations)

        # 10. Display results
        _print_result(result)

        # 11. Optionally write to file
        if args.output:
            out_path = pathlib.Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"\nFull state written to: {out_path}")

        return 0

    except Exception as exc:
        audit_logger.log_session_end(session_id, tier)
        if session_factory is not None:
            try:
                async with session_factory() as db:
                    await SessionRepo.end_session(
                        db,
                        uuid.UUID(session_id),
                        status="failed",
                        summary=str(exc),
                    )
                    await db.commit()
            except Exception:
                pass  # DB may not have tables
        print(f"\nWorkflow failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await mcp_ctx.__aexit__(None, None, None)
        if db_engine is not None:
            await db_engine.dispose()


def _print_result(result: dict) -> None:
    """Print a human-readable summary of the workflow result."""
    print("=" * 60)
    print("INCIDENT RESPONSE COMPLETE")
    print("=" * 60)

    if result.get("summary"):
        print(f"\n--- Summary ---\n{result['summary']}")

    if result.get("diagnosis"):
        print(f"\n--- Diagnosis ---\n{result['diagnosis']}")

    if result.get("verification"):
        print(f"\n--- Verification ---\n{result['verification']}")

    approved = result.get("approved_actions", [])
    blocked = result.get("blocked_actions", [])
    tool_calls = result.get("tool_calls", [])

    print(f"\n--- Actions ---")
    print(f"Planned:  {len(approved) + len(blocked)}")
    print(f"Approved: {len(approved)}")
    print(f"Blocked:  {len(blocked)}")
    print(f"Executed: {len(tool_calls)}")

    if blocked:
        print(f"\nBlocked actions:")
        for action in blocked:
            name = action.get("tool_name", "?")
            reason = action.get("block_reason", "unknown")
            print(f"  ✗ {name} — {reason}")

    if tool_calls:
        print(f"\nExecuted tool calls:")
        for tc in tool_calls:
            name = tc.get("tool_name", "?")
            status = "error" if tc.get("error") else "ok"
            dur = f" ({tc['duration_ms']}ms)" if tc.get("duration_ms") else ""
            print(f"  {'✓' if status == 'ok' else '✗'} {name} [{status}]{dur}")

    print(f"\nStatus: {result.get('status', 'unknown')}")
    print(f"Session: {result.get('session_id', 'unknown')}")


# -- serve -------------------------------------------------------------------


def _run_serve(args: argparse.Namespace) -> int:
    """Start uvicorn against ``backend.api.app:create_app``.

    Runs Alembic migrations first unless ``--skip-migrations`` is set. In a
    frozen binary ``backend.resource.bootstrap_bundled_env`` has already
    pointed the config at the embedded static-export and skill files.
    """
    import uvicorn
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from backend.resource import is_frozen, resource_path

    if not args.skip_migrations:
        alembic_ini = resource_path("alembic.ini")
        if alembic_ini.is_file():
            cfg = AlembicConfig(str(alembic_ini))
            # When frozen, migrations/ is packaged next to alembic.ini.
            cfg.set_main_option(
                "script_location",
                str(resource_path("backend/db/migrations")),
            )
            try:
                command.upgrade(cfg, "head")
            except Exception as exc:  # noqa: BLE001
                print(f"Alembic upgrade failed: {exc}", file=sys.stderr)
                return 1
        else:
            print(
                f"Warning: alembic.ini not found at {alembic_ini}; "
                "skipping migrations",
                file=sys.stderr,
            )

    # Reload only makes sense when running from source.
    reload = bool(args.reload) and not is_frozen()

    uvicorn.run(
        "backend.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=reload,
    )
    return 0


# -- main --------------------------------------------------------------------


def _run_saml_gen_sp_keys(args: argparse.Namespace) -> int:
    """Emit a self-signed SP keypair (PEM cert + key) to stdout.

    Designed for ``aim saml gen-sp-keys``. The output is meant to be copied into
    ``AIM_SAML_SP_CERT`` / ``AIM_SAML_SP_KEY`` in the operator's secret store —
    we never persist it to disk on the user's behalf to avoid silent leakage.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print(
            "cryptography is not installed. Install with: uv sync",
            file=sys.stderr,
        )
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, args.cn)]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=args.days))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    print("# === SAML SP CERT (copy into AIM_SAML_SP_CERT) ===")
    print(cert_pem)
    print("# === SAML SP KEY  (copy into AIM_SAML_SP_KEY) ===")
    print(key_pem)
    print(
        "# Reminder: store these in your secret manager and inject as env "
        "vars. AIM does not persist the keypair on disk."
    )
    return 0


def main(argv: list[str] | None = None) -> None:
    # When running as a PyInstaller bundle, point env vars at the extracted
    # resources (frontend/out, examples/SKILL.md). Must run BEFORE Config.load.
    from backend.resource import bootstrap_bundled_env
    bootstrap_bundled_env()

    args = _parse_args(argv)

    if args.version:
        print(importlib.metadata.version("ai-incident-manager"))
        sys.exit(0)

    # `serve` doesn't need the Config object loaded eagerly — the FastAPI app
    # factory reads its own AppConfig inside uvicorn's worker. Loading it here
    # would couple CLI startup to ~optional dependencies like asyncpg.
    if args.command == "serve":
        sys.exit(_run_serve(args))

    try:
        cfg = Config.load(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.command == "check":
        rc = asyncio.run(_run_check(cfg))
        sys.exit(rc)

    if args.command == "config":
        rc = _run_config(cfg, args)
        sys.exit(rc)

    if args.command == "run":
        rc = asyncio.run(_run_incident(cfg, args))
        sys.exit(rc)

    if args.command == "audit":
        rc = _run_audit(cfg, args)
        sys.exit(rc)

    if args.command == "approvals":
        rc = asyncio.run(_run_approvals(cfg, args))
        sys.exit(rc)

    if args.command == "saml":
        if args.saml_command == "gen-sp-keys":
            sys.exit(_run_saml_gen_sp_keys(args))
        # No subcommand — print saml help.
        _build_parser().parse_args(["saml", "--help"])
        sys.exit(0)

    # No subcommand — print help
    _build_parser().print_help()


if __name__ == "__main__":
    main()
