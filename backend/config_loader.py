"""Environment-based configuration loader for AI Incident Manager."""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any

from dotenv import dotenv_values

DEFAULT_ENV_FILE = ".env"
DEFAULT_LOCAL_POSTGRES_URL = "postgresql+asyncpg://aim:aim@localhost:5432/aim"
DEFAULT_LOCAL_SQLITE_URL = "sqlite+aiosqlite:///./aim-local.db"

_ENV_PATH_OVERRIDE: pathlib.Path | None = None


def set_env_path(path: pathlib.Path | str | None) -> None:
    """Override the env file path for subsequent ``load()`` calls."""
    global _ENV_PATH_OVERRIDE
    _ENV_PATH_OVERRIDE = None if path is None else pathlib.Path(path)


def _resolve_env_path(path: pathlib.Path | str | None) -> tuple[pathlib.Path, bool]:
    if path is not None:
        return pathlib.Path(path), True
    if _ENV_PATH_OVERRIDE is not None:
        return _ENV_PATH_OVERRIDE, True
    return pathlib.Path(DEFAULT_ENV_FILE), False


def _normalize_env(file_values: dict[str, str | None]) -> dict[str, str]:
    merged = {k: v for k, v in file_values.items() if v is not None}
    for key, value in os.environ.items():
        merged[key] = value
    return merged


def _read_env(path: pathlib.Path | str | None = None) -> tuple[pathlib.Path, dict[str, str]]:
    env_path, explicit = _resolve_env_path(path)
    if env_path.is_file():
        return env_path, _normalize_env(dotenv_values(env_path))
    if explicit:
        raise FileNotFoundError(f"Environment file not found: {env_path}")
    return env_path, _normalize_env({})


def _env_str(env: dict[str, str], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None or value == "":
        return default
    return value


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = _env_str(env, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _env_csv(env: dict[str, str], key: str, default: str) -> list[str]:
    raw = (_env_str(env, key, default) or default).strip()
    if raw == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclasses.dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    token: str | None = None

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

    output: str = "./logs/audit.jsonl"


@dataclasses.dataclass
class ApprovalConfig:
    """Approval-flow configuration."""

    timeout_seconds: int = 900


@dataclasses.dataclass
class IngestConfig:
    """External incident ingestion rate-limiting.

    rate_limit: max requests per window per token (0 = disabled)
    rate_window: window size in seconds
    """

    rate_limit: int = 60
    rate_window: int = 60


@dataclasses.dataclass
class AppSettings:
    """General app runtime settings."""

    name: str = "AI Incident Manager"
    version: str = "0.2.0"
    tier: int = 2
    log_level: str = "INFO"
    skill_definition_path: str = "./examples/SKILL.md"
    audit_output: str = "./logs/audit.jsonl"
    frontend_static_dir: str = "./frontend/out"


@dataclasses.dataclass
class DatabaseConfig:
    """Database URLs and local fallbacks."""

    url: str | None = None
    local_postgres_url: str = DEFAULT_LOCAL_POSTGRES_URL
    sqlite_url: str = DEFAULT_LOCAL_SQLITE_URL


@dataclasses.dataclass
class AuthConfig:
    """JWT auth settings."""

    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60


@dataclasses.dataclass
class CorsConfig:
    """CORS settings."""

    origins: list[str] = dataclasses.field(default_factory=lambda: ["*"])


@dataclasses.dataclass
class ProviderConfig:
    """Default provider and provider-specific settings."""

    active_provider: str = "ollama"
    active_model_id: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    anthropic_api_key_env_var: str = "ANTHROPIC_API_KEY"
    openai_api_key_env_var: str = "OPENAI_API_KEY"
    azure_openai_api_key_env_var: str = "AZURE_OPENAI_API_KEY"
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_deployment: str | None = None


def _parse_mcp_servers(env: dict[str, str]) -> list[MCPServerConfig]:
    raw = _env_str(env, "AIM_MCP_SERVERS_JSON", "[]") or "[]"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AIM_MCP_SERVERS_JSON must contain valid JSON") from exc
    if not isinstance(items, list):
        raise ValueError("AIM_MCP_SERVERS_JSON must decode to a list")

    servers: list[MCPServerConfig] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError("Each AIM_MCP_SERVERS_JSON entry must be an object")
        servers.append(
            MCPServerConfig(
                name=str(entry.get("name", "unnamed")),
                transport=str(entry.get("transport", "stdio")),
                command=entry.get("command"),
                args=entry.get("args"),
                env=entry.get("env") or entry.get("env_vars"),
                url=entry.get("url"),
                token=entry.get("token"),
            )
        )
    return servers


@dataclasses.dataclass
class AppConfig:
    """Top-level typed configuration container."""

    mcp_servers: list[MCPServerConfig]
    tiers: dict[str, Any]
    logging: dict[str, Any]
    audit: AuditConfig
    approvals: ApprovalConfig
    ingest: IngestConfig
    app: AppSettings
    db: DatabaseConfig
    auth: AuthConfig
    cors: CorsConfig
    providers: ProviderConfig
    env_file: str

    @classmethod
    def load(cls, path: pathlib.Path | str | None = None) -> AppConfig:
        env_path, env = _read_env(path)

        app = AppSettings(
            tier=_env_int(env, "AIM_TIER", 2),
            log_level=_env_str(env, "AIM_LOG_LEVEL", "INFO") or "INFO",
            skill_definition_path=_env_str(
                env,
                "AIM_SKILL_DEFINITION",
                "./examples/SKILL.md",
            )
            or "./examples/SKILL.md",
            audit_output=_env_str(env, "AIM_AUDIT_LOG", "./logs/audit.jsonl")
            or "./logs/audit.jsonl",
            frontend_static_dir=_env_str(
                env,
                "AIM_FRONTEND_STATIC_DIR",
                "./frontend/out",
            )
            or "./frontend/out",
        )
        audit = AuditConfig(output=app.audit_output)
        approvals = ApprovalConfig(
            timeout_seconds=_env_int(env, "AIM_APPROVAL_TIMEOUT_SECONDS", 900)
        )

        ingest = IngestConfig(
            rate_limit=_env_int(env, "AIM_INGEST_RATE_LIMIT", 60),
            rate_window=_env_int(env, "AIM_INGEST_RATE_WINDOW", 60),
        )

        return cls(
            mcp_servers=_parse_mcp_servers(env),
            tiers={"default": app.tier},
            logging={"level": app.log_level},
            audit=audit,
            approvals=approvals,
            ingest=ingest,
            app=app,
            db=DatabaseConfig(
                url=_env_str(env, "AIM_DATABASE_URL"),
                local_postgres_url=_env_str(
                    env,
                    "AIM_LOCAL_POSTGRES_URL",
                    DEFAULT_LOCAL_POSTGRES_URL,
                )
                or DEFAULT_LOCAL_POSTGRES_URL,
                sqlite_url=_env_str(
                    env,
                    "AIM_SQLITE_FALLBACK_URL",
                    DEFAULT_LOCAL_SQLITE_URL,
                )
                or DEFAULT_LOCAL_SQLITE_URL,
            ),
            auth=AuthConfig(
                jwt_secret=_env_str(
                    env,
                    "AIM_JWT_SECRET",
                    "dev-secret-change-in-production",
                )
                or "dev-secret-change-in-production",
                jwt_algorithm=_env_str(env, "AIM_JWT_ALGORITHM", "HS256")
                or "HS256",
                jwt_expire_minutes=_env_int(env, "AIM_JWT_EXPIRE_MINUTES", 60),
            ),
            cors=CorsConfig(origins=_env_csv(env, "AIM_CORS_ORIGINS", "*")),
            providers=ProviderConfig(
                active_provider=_env_str(env, "AIM_MODEL_PROVIDER", "ollama")
                or "ollama",
                active_model_id=_env_str(env, "AIM_MODEL_ID", "llama3.2")
                or "llama3.2",
                ollama_base_url=_env_str(env, "OLLAMA_BASE_URL", "http://localhost:11434")
                or "http://localhost:11434",
                azure_openai_endpoint=_env_str(env, "AZURE_OPENAI_ENDPOINT"),
                azure_openai_api_version=_env_str(env, "AZURE_OPENAI_API_VERSION"),
                azure_openai_deployment=_env_str(env, "AZURE_OPENAI_DEPLOYMENT"),
            ),
            env_file=str(env_path),
        )


Config = AppConfig
