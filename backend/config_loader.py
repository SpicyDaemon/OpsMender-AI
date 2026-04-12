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
    transport: str  # "stdio", "sse", or "http"
    # stdio fields
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    # sse / http fields
    url: Optional[str] = None
    token: Optional[str] = None

    def __post_init__(self) -> None:
        if self.transport not in ("stdio", "sse", "http"):
            raise ValueError(
                f"MCP server '{self.name}': transport must be 'stdio', 'sse', or 'http', "
                f"got '{self.transport}'"
            )
        if self.transport == "stdio" and not self.command:
            raise ValueError(
                f"MCP server '{self.name}': stdio transport requires 'command'"
            )
        if self.transport in ("sse", "http") and not self.url:
            raise ValueError(
                f"MCP server '{self.name}': {self.transport} transport requires 'url'"
            )


@dataclasses.dataclass
class AuditConfig:
    """Audit logger configuration."""

    output: str  # path to JSONL log file


@dataclasses.dataclass
class ApprovalConfig:
    """Approval-flow configuration."""

    timeout_seconds: int = 900


@dataclasses.dataclass
class Config:
    """Top-level configuration container."""

    mcp_servers: List[MCPServerConfig]
    tiers: Dict[str, Any]
    logging: Dict[str, Any]
    audit: AuditConfig
    approvals: ApprovalConfig

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
                    token=entry.get("token"),
                )
            )

        raw_audit = data.get("audit", {})
        audit = AuditConfig(
            output=raw_audit.get("output", "./logs/audit.jsonl"),
        )
        raw_approvals = data.get("approvals", {})
        approvals = ApprovalConfig(
            timeout_seconds=raw_approvals.get("timeout_seconds", 900),
        )

        return cls(
            mcp_servers=servers,
            tiers=data.get("tiers", {}),
            logging=data.get("logging", {}),
            audit=audit,
            approvals=approvals,
        )
