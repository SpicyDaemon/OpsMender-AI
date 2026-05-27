"""Factories for constructing LLM providers and workflow clients."""

from __future__ import annotations

from .base import LLM, LLMProvider
from .providers import (
    AnthropicProvider,
    BedrockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderLLMAdapter,
    StubProvider,
)


def create_provider(
    *,
    provider: str,
    model_id: str | None = None,
    max_tokens: int = 4096,
    echo: bool = False,
    response: str = "[stub]",
    api_key_env_var: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    provider_meta: dict[str, object] | None = None,
) -> LLMProvider:
    """Build a provider instance by provider name."""
    if provider == "anthropic":
        return AnthropicProvider(
            model=model_id or "claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            api_key_env_var=api_key_env_var or "ANTHROPIC_API_KEY",
        )
    if provider == "openai":
        return OpenAIProvider(
            model=model_id or "gpt-4o",
            max_tokens=max_tokens,
            api_key_env_var=api_key_env_var or "OPENAI_API_KEY",
            base_url=base_url,
            api_version=api_version,
            azure=False,
        )
    if provider == "azure_openai":
        return OpenAIProvider(
            model=model_id or "gpt-4o",
            max_tokens=max_tokens,
            api_key_env_var=api_key_env_var or "AZURE_OPENAI_API_KEY",
            base_url=base_url,
            api_version=api_version,
            azure=True,
        )
    if provider == "bedrock":
        provider_meta = provider_meta or {}
        return BedrockProvider(
            model=model_id or "anthropic.claude-sonnet-4-6",
            region=str(provider_meta.get("region") or ""),
            profile=str(provider_meta.get("profile") or "") or None,
            max_tokens=max_tokens,
        )
    if provider == "openai_compatible":
        if not base_url:
            raise ValueError("openai_compatible provider requires a base_url")
        return OpenAICompatibleProvider(
            model=model_id or "gpt-4o",
            base_url=base_url,
            max_tokens=max_tokens,
            api_key_env_var=api_key_env_var,
        )
    if provider == "ollama":
        return OllamaProvider(
            model=model_id or "llama3.2",
            base_url=base_url or "http://localhost:11434",
            max_tokens=max_tokens,
        )
    if provider == "stub":
        return StubProvider(
            response=response,
            echo=echo,
            model_id=model_id or "stub",
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


def create_llm(
    *,
    provider: str,
    model_id: str | None = None,
    max_tokens: int = 4096,
    echo: bool = False,
    response: str = "[stub]",
    api_key_env_var: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    provider_meta: dict[str, object] | None = None,
) -> LLM:
    """Build a workflow-facing LLM client from a provider."""
    return ProviderLLMAdapter(
        create_provider(
            provider=provider,
            model_id=model_id,
            max_tokens=max_tokens,
            echo=echo,
            response=response,
            api_key_env_var=api_key_env_var,
            base_url=base_url,
            api_version=api_version,
            provider_meta=provider_meta,
        )
    )
