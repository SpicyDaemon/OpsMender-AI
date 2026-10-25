"""Model settings for one-off completions outside AI sessions.

Used by the Skill Studio's AI assist and the ingest extractor. It lived with the
removed environment scan audits until M1-93.
"""

from __future__ import annotations

from typing import Any


def resolve_provider_kwargs(config, model_cfg) -> dict[str, Any]:
    """Build create_provider/create_llm kwargs from a DB model row OR env.

    Per-provider env-only path (when model_cfg is None):
      - ollama             → OLLAMA_BASE_URL
      - lmstudio           → LMSTUDIO_BASE_URL, else LM Studio's default URL
      - azure_openai       → AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_VERSION
      - openai_compatible  → OPSMENDER_OPENAI_COMPATIBLE_BASE_URL
                             + optional OPSMENDER_OPENAI_COMPATIBLE_API_KEY_ENV_VAR
      - others (anthropic / openai / bedrock / vertex_ai / qwen / deepseek /
        kimi) → no base_url needed; their credentials/region/project
        resolve via their own SDK conventions (env API keys, AWS chain,
        ADC), and qwen / deepseek / kimi use their vendor's default URL.
    """
    if model_cfg is not None:
        return {
            "provider": model_cfg.provider,
            "model_id": model_cfg.model_id,
            "api_key_env_var": model_cfg.api_key_env_var,
            "base_url": model_cfg.base_url,
            "api_version": model_cfg.api_version,
            "provider_meta": model_cfg.provider_meta,
            "max_tokens": model_cfg.max_tokens,
        }
    providers = config.providers
    active = providers.active_provider
    base_url: str | None
    api_key_env_var: str | None = None
    api_version: str | None = None
    if active == "ollama":
        base_url = providers.ollama_base_url
    elif active == "lmstudio":
        base_url = providers.lmstudio_base_url
    elif active == "azure_openai":
        base_url = providers.azure_openai_endpoint
        api_version = providers.azure_openai_api_version
    elif active == "openai_compatible":
        base_url = providers.openai_compatible_base_url
        api_key_env_var = providers.openai_compatible_api_key_env_var
    else:
        base_url = None
    return {
        "provider": active,
        "model_id": providers.active_model_id,
        "base_url": base_url,
        "api_key_env_var": api_key_env_var,
        "api_version": api_version,
    }
