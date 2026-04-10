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

from backend.audit.logger import AuditEntry, AuditLogger
from backend.config_loader import Config
from backend.mcp.client import MCPClientError, connect, list_tools


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aim", description="AI Incident Manager CLI")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="Validate config and MCP server connectivity")

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
    """Validate config and test connectivity to all configured MCP servers."""
    print(f"Config OK — {len(cfg.mcp_servers)} MCP server(s) configured\n")

    if not cfg.mcp_servers:
        print(
            "No MCP servers configured. Add entries under 'mcp_servers' in config.yaml.\n"
            "See config.yaml comments for examples."
        )
        return 0

    all_ok = True
    for server in cfg.mcp_servers:
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


# -- audit subcommand --------------------------------------------------------


def _format_entry(entry: AuditEntry) -> str:
    """Format a single audit entry as a human-readable line."""
    ts = entry.timestamp[:19].replace("T", " ")  # trim to seconds
    status = "\u2713" if entry.permitted else "\u2717"
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


# -- config subcommand -------------------------------------------------------


def _run_config(cfg: Config, args: argparse.Namespace) -> int:
    """Display or validate the current configuration."""
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
    print(f"Config file: {args.config}")
    print(f"Tier:        {cfg.tiers.get('default', '(not set)')}")
    print(f"Log level:   {cfg.logging.get('level', '(not set)')}")
    print(f"Audit log:   {cfg.audit.output}")
    print(f"MCP servers: {len(cfg.mcp_servers)}")
    for s in cfg.mcp_servers:
        detail = s.command if s.transport == "stdio" else s.url
        print(f"  - {s.name} ({s.transport}) → {detail}")
    if not cfg.mcp_servers:
        print("  (none configured)")
    return 0


def _validate_config(cfg: Config, args: argparse.Namespace) -> int:
    """Validate config and optionally a skill file. Return 0 if valid."""
    errors: list[str] = []

    # Tier validation
    default_tier = cfg.tiers.get("default")
    if default_tier is None:
        errors.append("tiers.default is not set")
    elif default_tier not in (0, 1, 2, 3):
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
            print(f"  ✗ {err}")
        return 1

    print("Validation OK — configuration is valid.")
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

    # 5. Connect to MCP server(s) if available
    mcp_session = None
    mcp_ctx = contextlib.AsyncExitStack()

    try:
        await mcp_ctx.__aenter__()

        if cfg.mcp_servers and not args.dry_run:
            server = cfg.mcp_servers[0]
            print(f"Connecting to MCP server: {server.name} ({server.transport})...")
            try:
                mcp_session = await mcp_ctx.enter_async_context(connect(server))
                print(f"Connected to {server.name}.\n")
            except Exception as exc:
                print(f"MCP connection failed: {exc}", file=sys.stderr)
                print("Continuing without MCP (no tool execution).\n")
                mcp_session = None

        # 6. Log session start
        audit_logger.log_session_start(session_id, tier)

        # 7. Build and invoke the workflow graph
        graph = build_graph(
            tier=tier,
            skill_def=skill_def,
            llm=llm,
            mcp_session=mcp_session,
            audit_logger=audit_logger if mcp_session else None,
        )

        print("Running workflow: observe → diagnose → plan → tier_gate → execute → verify → summarize\n")

        result = await graph.ainvoke({
            "session_id": session_id,
            "tier": tier,
            "incident_description": args.incident,
            "skill_definition_path": str(skill_path),
        })

        # 8. Log session end
        audit_logger.log_session_end(session_id, tier)

        # 9. Display results
        _print_result(result)

        # 10. Optionally write to file
        if args.output:
            out_path = pathlib.Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"\nFull state written to: {out_path}")

        return 0

    except Exception as exc:
        audit_logger.log_session_end(session_id, tier)
        print(f"\nWorkflow failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await mcp_ctx.__aexit__(None, None, None)


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


# -- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.version:
        print(importlib.metadata.version("ai-incident-manager"))
        sys.exit(0)

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

    # No subcommand — print help
    _build_parser().print_help()


if __name__ == "__main__":
    main()
