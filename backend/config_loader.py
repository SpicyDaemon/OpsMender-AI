"""Configuration loader for AI Incident Manager.

Reads a YAML config file and returns typed dataclass objects.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Dict, List, Optional

import yaml


@dataclasses.dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str  # "stdio" or "sse"
    # stdio fields
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    # sse fields
    url: Optional[str] = None
    token_env: Optional[str] = None

    def __post_init__(self) -> None:
        if self.transport not in ("stdio", "sse"):
            raise ValueError(
                f"MCP server '{self.name}': transport must be 'stdio' or 'sse', "
                f"got '{self.transport}'"
            )
        if self.transport == "stdio" and not self.command:
            raise ValueError(
                f"MCP server '{self.name}': stdio transport requires 'command'"
            )
        if self.transport == "sse" and not self.url:
            raise ValueError(
                f"MCP server '{self.name}': sse transport requires 'url'"
            )


@dataclasses.dataclass
class Config:
    """Top-level configuration container."""

    mcp_servers: List[MCPServerConfig]
    tiers: Dict[str, Any]
    logging: Dict[str, Any]

    @classmethod
    def load(cls, path: pathlib.Path | str = "config.yaml") -> Config:
        """Load configuration from a YAML file."""
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        raw_servers = data.get("mcp_servers") or []
        servers = []
        for entry in raw_servers:
            servers.append(
                MCPServerConfig(
                    name=entry.get("name", "unnamed"),
                    transport=entry.get("transport", "stdio"),
                    command=entry.get("command"),
                    args=entry.get("args"),
                    env=entry.get("env"),
                    url=entry.get("url"),
                    token_env=entry.get("token_env"),
                )
            )

        return cls(
            mcp_servers=servers,
            tiers=data.get("tiers", {}),
            logging=data.get("logging", {}),
        )
