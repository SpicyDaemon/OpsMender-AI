"""Entry point for the ``aim`` command.

Supports subcommands and global options.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import sys

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

    # No subcommand — print loaded config (placeholder for future default)
    print("Configuration loaded:")
    print(cfg)


if __name__ == "__main__":
    main()
