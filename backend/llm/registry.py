"""Provider registry and validation helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .factory import create_provider


# Module-level cache for ``ProviderRegistry.discover_models`` results.
# Discovery is invoked on every ``GET /models`` (page paint of
# /dashboard/models) and walks every configured provider with a live
# round-trip. Operators rarely change provider keys mid-session, so a
# short TTL trades a little staleness for a huge wall-clock win on
# repeat loads. The cache key carries every discovery param so distinct
# queries don't collide.
_DISCOVERY_CACHE: dict[
    tuple[str | None, str | None, str | None, str | None, str | None, tuple[tuple[str, str], ...]],
    tuple[float, list[dict[str, object]]],
] = {}
_DISCOVERY_CACHE_DEFAULT_TTL_SECONDS = 60.0
# Sprint 62 — per-provider TTL overrides. Cloud catalogs (Bedrock,
# Vertex, OCI) change rarely, so a 1-hour cache trades little freshness
# for much faster repeat loads. Local + OpenAI-compatible endpoints can
# change during development; keep them at the default short TTL.
_DISCOVERY_CACHE_TTL_OVERRIDES: dict[str | None, float] = {
    "bedrock": 3600.0,
    "vertex_ai": 3600.0,
    "oci_genai": 3600.0,
    "azure_ai_foundry": 3600.0,
}


def _ttl_for(provider: str | None) -> float:
    if provider is None:
        return _DISCOVERY_CACHE_DEFAULT_TTL_SECONDS
    return _DISCOVERY_CACHE_TTL_OVERRIDES.get(
        provider, _DISCOVERY_CACHE_DEFAULT_TTL_SECONDS
    )


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    label: str
    default_model_id: str
    default_api_key_env_var: str | None = None
    requires_api_key: bool = True
    requires_base_url: bool = False
    requires_api_version: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    provider: str
    model_id: str
    warnings: list[ValidationIssue] = field(default_factory=list)
    discovered_models: list[str] = field(default_factory=list)
    discovery_error: str | None = None


class ProviderRegistry:
    """Registry for supported LLM providers."""

    _SPECS: dict[str, ProviderSpec] = {
        "anthropic": ProviderSpec(
            provider="anthropic",
            label="Anthropic",
            default_model_id="claude-sonnet-4-20250514",
            default_api_key_env_var="ANTHROPIC_API_KEY",
        ),
        "openai": ProviderSpec(
            provider="openai",
            label="OpenAI",
            default_model_id="gpt-4o",
            default_api_key_env_var="OPENAI_API_KEY",
        ),
        "azure_openai": ProviderSpec(
            provider="azure_openai",
            label="Azure OpenAI",
            default_model_id="gpt-4o",
            default_api_key_env_var="AZURE_OPENAI_API_KEY",
            requires_base_url=True,
            requires_api_version=True,
        ),
        "bedrock": ProviderSpec(
            provider="bedrock",
            label="AWS Bedrock",
            default_model_id="anthropic.claude-sonnet-4-6",
            requires_api_key=False,
        ),
        "vertex_ai": ProviderSpec(
            provider="vertex_ai",
            label="GCP Vertex AI",
            default_model_id="google/gemini-2.5-flash",
            requires_api_key=False,
        ),
        "ollama": ProviderSpec(
            provider="ollama",
            label="Ollama",
            default_model_id="llama3.2",
            requires_api_key=False,
        ),
        # Sprint 62 Step 1 — generic OpenAI-API-compatible endpoint.
        # Covers vLLM, LM Studio, OpenRouter, Together, Groq, Fireworks,
        # Anyscale, and most local OpenAI-shape runtimes. base_url is
        # required (that's what distinguishes it from the plain
        # ``openai`` provider); api_key is optional because some local
        # endpoints don't enforce auth.
        "openai_compatible": ProviderSpec(
            provider="openai_compatible",
            label="OpenAI-compatible",
            default_model_id="gpt-4o",
            default_api_key_env_var=None,
            requires_api_key=False,
            requires_base_url=True,
        ),
    }

    def list_specs(self) -> list[ProviderSpec]:
        return [self._SPECS[name] for name in sorted(self._SPECS)]

    def get_spec(self, provider: str) -> ProviderSpec:
        try:
            return self._SPECS[provider]
        except KeyError as exc:
            raise ValueError(f"Unsupported LLM provider: {provider}") from exc

    @classmethod
    def clear_discovery_cache(cls) -> None:
        """Drop the discovery TTL cache. Test-only helper."""
        _DISCOVERY_CACHE.clear()

    def discover_models(
        self,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        provider_meta: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> list[dict[str, object]]:
        provider_meta = provider_meta or {}
        provider_meta_key = tuple(sorted(provider_meta.items()))
        cache_key = (
            provider,
            model_id,
            api_key_env_var,
            base_url,
            api_version,
            provider_meta_key,
        )
        ttl = _ttl_for(provider)
        if use_cache:
            hit = _DISCOVERY_CACHE.get(cache_key)
            if hit is not None and time.monotonic() - hit[0] < ttl:
                return hit[1]

        providers = [provider] if provider else [spec.provider for spec in self.list_specs()]
        results: list[dict[str, object]] = []
        for provider_name in providers:
            spec = self.get_spec(provider_name)
            selected_model = model_id or spec.default_model_id
            selected_api_key = api_key_env_var or spec.default_api_key_env_var
            try:
                client = create_provider(
                    provider=provider_name,
                    model_id=selected_model,
                    api_key_env_var=selected_api_key,
                    base_url=base_url,
                    api_version=api_version,
                    provider_meta=provider_meta,
                )
                models = client.list_models()
                results.append({
                    "provider": provider_name,
                    "label": spec.label,
                    "default_model_id": spec.default_model_id,
                    "default_api_key_env_var": spec.default_api_key_env_var,
                    "requires_api_key": spec.requires_api_key,
                    "requires_base_url": spec.requires_base_url,
                    "requires_api_version": spec.requires_api_version,
                    "available": True,
                    "models": models,
                    "error": None,
                })
            except Exception as exc:
                results.append({
                    "provider": provider_name,
                    "label": spec.label,
                    "default_model_id": spec.default_model_id,
                    "default_api_key_env_var": spec.default_api_key_env_var,
                    "requires_api_key": spec.requires_api_key,
                    "requires_base_url": spec.requires_base_url,
                    "requires_api_version": spec.requires_api_version,
                    "available": False,
                    "models": [],
                    "error": str(exc),
                })
        if use_cache:
            _DISCOVERY_CACHE[cache_key] = (time.monotonic(), results)
        return results

    def validate_model_config(
        self,
        *,
        provider: str,
        model_id: str,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
        provider_meta: dict[str, str] | None = None,
        allow_unverified: bool = False,
    ) -> ValidationResult:
        spec = self.get_spec(provider)
        provider_meta = provider_meta or {}

        if spec.requires_base_url and not base_url:
            raise ValueError(f"Provider '{provider}' requires a base_url")
        if spec.requires_api_version and not api_version:
            raise ValueError(f"Provider '{provider}' requires an api_version")
        if provider == "bedrock" and not provider_meta.get("region"):
            raise ValueError("Provider 'bedrock' requires provider_meta.region")
        if provider == "vertex_ai" and not provider_meta.get("project"):
            raise ValueError("Provider 'vertex_ai' requires provider_meta.project")
        if provider == "vertex_ai" and not provider_meta.get("location"):
            raise ValueError("Provider 'vertex_ai' requires provider_meta.location")

        warnings: list[ValidationIssue] = []
        discovery_error: str | None = None
        discovered_models: list[str] = []
        selected_api_key = api_key_env_var or spec.default_api_key_env_var

        try:
            client = create_provider(
                provider=provider,
                model_id=model_id,
                api_key_env_var=selected_api_key,
                base_url=base_url,
                api_version=api_version,
                provider_meta=provider_meta,
            )
            discovered_models = client.list_models()
        except Exception as exc:
            discovery_error = str(exc)
            if not allow_unverified:
                raise ValueError(discovery_error) from exc
            warnings.append(
                ValidationIssue(
                    code="provider_unverified",
                    message=(
                        "Could not verify provider connectivity during validation: "
                        f"{discovery_error}. Saving anyway because explicit bootstrap "
                        "inputs are allowed."
                    ),
                )
            )
            return ValidationResult(
                provider=provider,
                model_id=model_id,
                warnings=warnings,
                discovered_models=discovered_models,
                discovery_error=discovery_error,
            )

        if provider in {"openai", "ollama", "openai_compatible"} and discovered_models and model_id not in discovered_models:
            message = (
                f"Model '{model_id}' is not currently reported by provider '{provider}'. "
                f"Discovered models: {', '.join(discovered_models)}"
            )
            if not allow_unverified:
                raise ValueError(message)
            warnings.append(
                ValidationIssue(
                    code="model_not_reported",
                    message=f"{message}. Saving anyway because manual model IDs are allowed.",
                )
            )

        return ValidationResult(
            provider=provider,
            model_id=model_id,
            warnings=warnings,
            discovered_models=discovered_models,
            discovery_error=discovery_error,
        )
