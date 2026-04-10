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

    if args.command == "audit":
        rc = _run_audit(cfg, args)
        sys.exit(rc)

    # No subcommand — print loaded config (placeholder for future default)
    print("Configuration loaded:")
    print(cfg)


if __name__ == "__main__":
    main()
