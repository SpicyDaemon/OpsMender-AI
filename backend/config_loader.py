"""Environment-based configuration loader for OpsMender AI."""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any

from dotenv import dotenv_values

DEFAULT_ENV_FILE = ".env"
DEFAULT_LOCAL_POSTGRES_URL = "postgresql+asyncpg://opsmender:opsmender@localhost:5432/opsmender"
DEFAULT_LOCAL_SQLITE_URL = "sqlite+aiosqlite:///./opsmender-local.db"

# Sprint 43 P0 #4 — default JWT secrets that MUST be replaced before a
# production deployment. The startup guard refuses to start the API
# when any of these is in effect unless OPSMENDER_DEPLOYMENT_MODE is
# explicitly set to "development".
_DEFAULT_JWT_SECRETS: frozenset[str] = frozenset(
    {
        "change-me-in-production",  # .env.example default
        "dev-secret-change-in-production",  # AuthConfig dataclass default
    }
)


class InsecureProductionConfigError(RuntimeError):
    """Raised when the API would start in production with an unsafe default."""


def check_production_safety(config: "AppConfig") -> None:
    """Refuse to start the API in production with an unset JWT secret.

    Activated when ``OPSMENDER_DEPLOYMENT_MODE`` is unset or set to any
    value other than ``"development"``. ``scripts/dev_server.py`` sets
    the env var to ``development`` so local dev keeps working.
    """
    mode = (os.environ.get("OPSMENDER_DEPLOYMENT_MODE") or "").strip().lower()
    if mode == "development":
        return
    secret = (config.auth.jwt_secret or "").strip()
    if secret in _DEFAULT_JWT_SECRETS:
        raise InsecureProductionConfigError(
            "OPSMENDER_JWT_SECRET is still the default placeholder "
            f"({secret!r}). Set a strong value before starting the API in "
            "production — e.g. `openssl rand -hex 32` — or set "
            "OPSMENDER_DEPLOYMENT_MODE=development for local dev."
        )

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


def _env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = _env_str(env, key)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be a boolean, got {raw!r}")


def _env_csv(env: dict[str, str], key: str, default: str) -> list[str]:
    raw = (_env_str(env, key, default) or default).strip()
    if raw == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_severity(
    env: dict[str, str],
    key: str,
    default: str,
) -> str:
    raw = (_env_str(env, key, default) or default).strip().lower()
    if raw not in {"critical", "high", "medium", "low"}:
        raise ValueError(
            f"{key} must be one of critical|high|medium|low, got {raw!r}"
        )
    return raw


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
    auto_start_enabled: bool = False
    auto_start_min_severity: str = "critical"
    auto_start_source: str | None = None


@dataclasses.dataclass
class SLAConfig:
    """SLA polling settings."""

    poller_enabled: bool = False
    poll_interval_default: int = 60


@dataclasses.dataclass
class Tier0Config:
    """Tier 0 sandbox hard time limits (Sprint 17)."""

    max_session_seconds: int = 600
    max_node_seconds: int = 120


@dataclasses.dataclass
class AppSettings:
    """General app runtime settings."""

    name: str = "OpsMender AI"
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
class SAMLConfig:
    """Global SAML SP settings (Sprint 30).

    Per-tenant IdP details live in the ``org_saml_configs`` table. This
    dataclass only carries the SP-side keypair + optional entityId override
    that's shared across all tenants.

    SAML is treated as **disabled** unless both ``sp_cert`` and ``sp_key`` are
    populated; the routes will return 400 and the login UI hides the button.
    """

    sp_cert: str | None = None
    sp_key: str | None = None
    sp_entity_id: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.sp_cert) and bool(self.sp_key)


@dataclasses.dataclass
class BotOAuthConfig:
    """Shared OAuth client credentials for bot-connector "Connect to …"
    flows (Sprint 31 Step 5–6).

    Like the SAML SP keypair, these are global secrets supplied at deploy
    time and never persisted in the DB. A platform is treated as
    **OAuth-enabled** only when both ``client_id`` and ``client_secret``
    are populated; otherwise the UI falls back to manual paste.
    """

    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    discord_client_id: str | None = None
    discord_client_secret: str | None = None

    def is_enabled(self, platform: str) -> bool:
        if platform == "slack":
            return bool(self.slack_client_id) and bool(self.slack_client_secret)
        if platform == "discord":
            return bool(self.discord_client_id) and bool(self.discord_client_secret)
        return False


@dataclasses.dataclass
class CorsConfig:
    """CORS settings."""

    origins: list[str] = dataclasses.field(default_factory=lambda: ["*"])


@dataclasses.dataclass
class PeopleConfig:
    """Sprint 56 — user-management surface.

    `bootstrap_admin_email` + `bootstrap_admin_password` create the first
    admin when the users table is empty. Both must be set together; if
    either is missing or the table already has rows, bootstrap is a no-op.

    `multi_org_enabled` gates multi-tenant UI affordances (org switcher,
    "create another organization", per-invite org picker). The
    multi-tenant schema + SSO/SAML-per-tenant work from Sprints 29-30
    stays intact regardless — only the UI changes.

    `public_base_url` is the absolute URL the dashboard is reachable at;
    invite + password-reset URLs use it. Falls back to the request's
    derived base when unset, but should be set for production.
    """

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    multi_org_enabled: bool = False
    public_base_url: str | None = None

    @property
    def bootstrap_configured(self) -> bool:
        return bool(self.bootstrap_admin_email) and bool(self.bootstrap_admin_password)


@dataclasses.dataclass
class SMTPConfig:
    """Best-effort outbound email for invites + password resets (Sprint 56).

    SMTP is treated as **configured** when `host` and `from_address` are
    both set. Failures during send are logged but never raise — the
    copy-paste URL returned by the route is always the source of truth.
    """

    host: str | None = None
    port: int = 587
    user: str | None = None
    password: str | None = None
    from_address: str | None = None
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host) and bool(self.from_address)


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
    raw = _env_str(env, "OPSMENDER_MCP_SERVERS_JSON", "[]") or "[]"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OPSMENDER_MCP_SERVERS_JSON must contain valid JSON") from exc
    if not isinstance(items, list):
        raise ValueError("OPSMENDER_MCP_SERVERS_JSON must decode to a list")

    servers: list[MCPServerConfig] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError("Each OPSMENDER_MCP_SERVERS_JSON entry must be an object")
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
    sla: SLAConfig
    tier0: "Tier0Config"
    app: AppSettings
    db: DatabaseConfig
    auth: AuthConfig
    saml: SAMLConfig
    bot_oauth: BotOAuthConfig
    cors: CorsConfig
    providers: ProviderConfig
    people: PeopleConfig
    smtp: SMTPConfig
    env_file: str

    @classmethod
    def load(cls, path: pathlib.Path | str | None = None) -> AppConfig:
        env_path, env = _read_env(path)

        app = AppSettings(
            tier=_env_int(env, "OPSMENDER_TIER", 2),
            log_level=_env_str(env, "OPSMENDER_LOG_LEVEL", "INFO") or "INFO",
            skill_definition_path=_env_str(
                env,
                "OPSMENDER_SKILL_DEFINITION",
                "./examples/SKILL.md",
            )
            or "./examples/SKILL.md",
            audit_output=_env_str(env, "OPSMENDER_AUDIT_LOG", "./logs/audit.jsonl")
            or "./logs/audit.jsonl",
            frontend_static_dir=_env_str(
                env,
                "OPSMENDER_FRONTEND_STATIC_DIR",
                "./frontend/out",
            )
            or "./frontend/out",
        )
        audit = AuditConfig(output=app.audit_output)
        approvals = ApprovalConfig(
            timeout_seconds=_env_int(env, "OPSMENDER_APPROVAL_TIMEOUT_SECONDS", 900)
        )

        ingest = IngestConfig(
            rate_limit=_env_int(env, "OPSMENDER_INGEST_RATE_LIMIT", 60),
            rate_window=_env_int(env, "OPSMENDER_INGEST_RATE_WINDOW", 60),
            auto_start_enabled=_env_bool(
                env, "OPSMENDER_INGEST_AUTO_START_ENABLED", False
            ),
            auto_start_min_severity=_env_severity(
                env, "OPSMENDER_INGEST_AUTO_START_MIN_SEVERITY", "critical"
            ),
            auto_start_source=(
                (_env_str(env, "OPSMENDER_INGEST_AUTO_START_SOURCE", "") or "").strip().lower()
                or None
            ),
        )
        sla = SLAConfig(
            poller_enabled=_env_bool(env, "OPSMENDER_SLA_POLLER_ENABLED", False),
            poll_interval_default=_env_int(env, "OPSMENDER_SLA_POLL_INTERVAL_DEFAULT", 60),
        )
        tier0 = Tier0Config(
            max_session_seconds=_env_int(
                env, "OPSMENDER_TIER0_MAX_SESSION_SECONDS", 600
            ),
            max_node_seconds=_env_int(env, "OPSMENDER_TIER0_MAX_NODE_SECONDS", 120),
        )

        return cls(
            mcp_servers=_parse_mcp_servers(env),
            tiers={"default": app.tier},
            logging={"level": app.log_level},
            audit=audit,
            approvals=approvals,
            ingest=ingest,
            sla=sla,
            tier0=tier0,
            app=app,
            db=DatabaseConfig(
                url=_env_str(env, "OPSMENDER_DATABASE_URL"),
                local_postgres_url=_env_str(
                    env,
                    "OPSMENDER_LOCAL_POSTGRES_URL",
                    DEFAULT_LOCAL_POSTGRES_URL,
                )
                or DEFAULT_LOCAL_POSTGRES_URL,
                sqlite_url=_env_str(
                    env,
                    "OPSMENDER_SQLITE_FALLBACK_URL",
                    DEFAULT_LOCAL_SQLITE_URL,
                )
                or DEFAULT_LOCAL_SQLITE_URL,
            ),
            auth=AuthConfig(
                jwt_secret=_env_str(
                    env,
                    "OPSMENDER_JWT_SECRET",
                    "dev-secret-change-in-production",
                )
                or "dev-secret-change-in-production",
                jwt_algorithm=_env_str(env, "OPSMENDER_JWT_ALGORITHM", "HS256")
                or "HS256",
                jwt_expire_minutes=_env_int(env, "OPSMENDER_JWT_EXPIRE_MINUTES", 60),
            ),
            saml=SAMLConfig(
                sp_cert=_env_str(env, "OPSMENDER_SAML_SP_CERT"),
                sp_key=_env_str(env, "OPSMENDER_SAML_SP_KEY"),
                sp_entity_id=_env_str(env, "OPSMENDER_SAML_SP_ENTITY_ID"),
            ),
            bot_oauth=BotOAuthConfig(
                slack_client_id=_env_str(env, "OPSMENDER_SLACK_OAUTH_CLIENT_ID"),
                slack_client_secret=_env_str(env, "OPSMENDER_SLACK_OAUTH_CLIENT_SECRET"),
                discord_client_id=_env_str(env, "OPSMENDER_DISCORD_OAUTH_CLIENT_ID"),
                discord_client_secret=_env_str(env, "OPSMENDER_DISCORD_OAUTH_CLIENT_SECRET"),
            ),
            cors=CorsConfig(origins=_env_csv(env, "OPSMENDER_CORS_ORIGINS", "*")),
            providers=ProviderConfig(
                active_provider=_env_str(env, "OPSMENDER_MODEL_PROVIDER", "ollama")
                or "ollama",
                active_model_id=_env_str(env, "OPSMENDER_MODEL_ID", "llama3.2")
                or "llama3.2",
                ollama_base_url=_env_str(env, "OLLAMA_BASE_URL", "http://localhost:11434")
                or "http://localhost:11434",
                azure_openai_endpoint=_env_str(env, "AZURE_OPENAI_ENDPOINT"),
                azure_openai_api_version=_env_str(env, "AZURE_OPENAI_API_VERSION"),
                azure_openai_deployment=_env_str(env, "AZURE_OPENAI_DEPLOYMENT"),
            ),
            people=PeopleConfig(
                bootstrap_admin_email=_env_str(env, "OPSMENDER_BOOTSTRAP_ADMIN_EMAIL"),
                bootstrap_admin_password=_env_str(
                    env, "OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD"
                ),
                multi_org_enabled=_env_bool(env, "OPSMENDER_MULTI_ORG_ENABLED", False),
                public_base_url=_env_str(env, "OPSMENDER_PUBLIC_BASE_URL"),
            ),
            smtp=SMTPConfig(
                host=_env_str(env, "OPSMENDER_SMTP_HOST"),
                port=_env_int(env, "OPSMENDER_SMTP_PORT", 587),
                user=_env_str(env, "OPSMENDER_SMTP_USER"),
                password=_env_str(env, "OPSMENDER_SMTP_PASSWORD"),
                from_address=_env_str(env, "OPSMENDER_SMTP_FROM"),
                use_tls=_env_bool(env, "OPSMENDER_SMTP_USE_TLS", True),
            ),
            env_file=str(env_path),
        )


Config = AppConfig
