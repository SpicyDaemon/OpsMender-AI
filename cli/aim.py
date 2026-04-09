"""Entry point for the ``aim`` command.

The CLI is intentionally minimal for the initial scaffold.  It supports a
``--config`` option to override the default configuration file and a
``--version`` flag.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys

from backend.config_loader import Config


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.version:
        print(importlib.metadata.version("ai-incident-manager"))
        sys.exit(0)
    try:
        cfg = Config.load(args.config)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Error loading config: {exc}", file=sys.stderr)
        sys.exit(1)
    # For now, just echo the loaded config to demonstrate the loader.
    print("Configuration loaded:")
    print(cfg)


if __name__ == "__main__":  # pragma: no cover
    main()