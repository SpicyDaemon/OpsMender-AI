"""Config endpoints.

GET  /config — read current system configuration
PUT  /config — update config (admin only)
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.auth import get_current_user, require_role
from backend.api.schemas import ConfigResponse, ConfigUpdate
from backend.config_loader import Config
from backend.db.models import User

router = APIRouter(prefix="/config", tags=["config"])

# ---------------------------------------------------------------------------
# Configurable path (can be overridden for tests)
# ---------------------------------------------------------------------------

_config_path: pathlib.Path = pathlib.Path("config.yaml")


def set_config_path(path: pathlib.Path | str) -> None:
    """Override the config file path (useful for testing)."""
    global _config_path
    _config_path = pathlib.Path(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> Config:
    """Load the current config from disk."""
    try:
        return Config.load(_config_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="config.yaml not found",
        )


def _config_to_response(cfg: Config) -> ConfigResponse:
    """Convert a Config object to the API response schema."""
    servers = []
    for s in cfg.mcp_servers:
        entry: dict = {"name": s.name, "transport": s.transport}
        if s.url:
            entry["url"] = s.url
        if s.command:
            entry["command"] = s.command
        if s.args:
            entry["args"] = s.args
        servers.append(entry)

    return ConfigResponse(
        tier=cfg.tiers.get("default", 2),
        mcp_servers=servers,
        audit_output=cfg.audit.output,
        logging_level=cfg.logging.get("level", "INFO"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ConfigResponse,
    summary="Get current system config",
)
async def get_config(
    user: User = Depends(require_role("admin", "operator")),
):
    cfg = _load_config()
    return _config_to_response(cfg)


@router.put(
    "",
    response_model=ConfigResponse,
    summary="Update system config (admin only)",
)
async def update_config(
    body: ConfigUpdate,
    user: User = Depends(require_role("admin")),
):
    import yaml

    _load_config()  # validate current config is loadable

    # Read raw YAML so we can patch and re-write
    with _config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if body.tier is not None:
        raw.setdefault("tiers", {})["default"] = body.tier
    if body.logging_level is not None:
        raw.setdefault("logging", {})["level"] = body.logging_level

    with _config_path.open("w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

    # Re-load and return updated config
    updated = Config.load(_config_path)
    return _config_to_response(updated)
