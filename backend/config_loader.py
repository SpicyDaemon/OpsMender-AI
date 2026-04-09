"""Configuration loader for AI Incident Manager.

The loader reads a YAML file (default ``config.yaml`` in the project root) and
provides a simple dataclass interface for accessing configuration values.
It is intentionally lightweight to keep the initial scaffold minimal.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Dict

import yaml


@dataclasses.dataclass
class Config:
    """Simple configuration container.

    Attributes are populated from the YAML file.  The dataclass is kept
    minimal; additional attributes can be added as the project evolves.
    """

    mcp: Dict[str, Any]
    tiers: Dict[str, Any]
    logging: Dict[str, Any]

    @classmethod
    def load(cls, path: pathlib.Path | str = "config.yaml") -> "Config":
        """Load configuration from *path*.

        Parameters
        ----------
        path:
            Path to the YAML configuration file.  Defaults to ``config.yaml``
            in the current working directory.
        """
        p = pathlib.Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(
            mcp=data.get("mcp", {}),
            tiers=data.get("tiers", {}),
            logging=data.get("logging", {}),
        )
