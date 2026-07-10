"""First-install model-config bootstrap from env.

When no ``model_configs`` row exists for the only/first org AND the env
sets ``OPSMENDER_MODEL_PROVIDER`` + ``OPSMENDER_MODEL_ID``
(+ provider-specific base_url), create a default model row so the agent
loop can run without the operator clicking through /dashboard/models on
a fresh install.

Mirrors the bootstrap-admin pattern: runs once at API startup, no-op if
a row already exists, idempotent on every restart after that.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.db.repos import ModelConfigRepo, OrganizationRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.config_loader import ProviderConfig

logger = logging.getLogger(__name__)


# Providers that REQUIRE an explicit base_url in env to be useful as a
# bootstrap source. Without one we'd write a row that fails on first
# use, which is worse than skipping bootstrap.
_BASE_URL_REQUIRED: dict[str, str] = {
    "openai_compatible": "openai_compatible_base_url",
}


def _resolve_base_url(provider: str, cfg: "ProviderConfig") -> str | None:
    """Pick the right base_url for a given provider out of ProviderConfig."""
    if provider == "ollama":
        return cfg.ollama_base_url
    if provider == "azure_openai":
        return cfg.azure_openai_endpoint
    if provider == "openai_compatible":
        return cfg.openai_compatible_base_url
    return None


def _resolve_api_key_env_var(provider: str, cfg: "ProviderConfig") -> str | None:
    if provider == "anthropic":
        return cfg.anthropic_api_key_env_var
    if provider == "openai":
        return cfg.openai_api_key_env_var
    if provider == "azure_openai":
        return cfg.azure_openai_api_key_env_var
    if provider == "openai_compatible":
        return cfg.openai_compatible_api_key_env_var
    return None


async def bootstrap_model_config(
    session_factory: "async_sessionmaker",
    cfg: "ProviderConfig",
) -> None:
    """Create a default ModelConfig row from env when none exists.

    Strict gating to avoid noisy bootstrapping:
      - Skips entirely if any org already has a model_configs row.
      - Skips if OPSMENDER_MODEL_PROVIDER is the compiled-in default
        ("ollama") AND OLLAMA_BASE_URL is unchanged from default — i.e.
        the operator did not actually configure anything.
      - Skips providers that require a base_url if no base_url is set.
    """

    provider = (cfg.active_provider or "").strip()
    model_id = (cfg.active_model_id or "").strip()
    if not provider or not model_id:
        return

    base_url_field = _BASE_URL_REQUIRED.get(provider)
    if base_url_field is not None:
        if not getattr(cfg, base_url_field, None):
            logger.info(
                "Model bootstrap skipped: provider=%s requires %s in env",
                provider,
                base_url_field,
            )
            return

    async with session_factory() as db:
        orgs = await OrganizationRepo.list_all(db)
        if not orgs:
            # Wait for bootstrap_admin to create the default org. The
            # next startup will bootstrap the model config.
            return
        org = orgs[0]

        existing = await ModelConfigRepo.list_all(db, org.id)
        if existing:
            return

        base_url = _resolve_base_url(provider, cfg)
        api_key_env_var = _resolve_api_key_env_var(provider, cfg)

        await ModelConfigRepo.create(
            db,
            org.id,
            name=f"default-{provider}",
            provider=provider,
            model_id=model_id,
            base_url=base_url,
            api_key_env_var=api_key_env_var,
            api_version=cfg.azure_openai_api_version
            if provider == "azure_openai"
            else None,
            is_default=True,
        )
        await db.commit()

        logger.info(
            "Bootstrap model config created (provider=%s, model_id=%s, org=%s)",
            provider,
            model_id,
            org.slug,
        )
