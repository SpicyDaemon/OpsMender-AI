"""Provider registry and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .factory import create_provider


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    label: str
    default_model_id: str
    default_api_key_env_var: str | None = None
    requires_api_key: bool = True
    requires_base_url: bool = False
    requires_api_version: bool = False


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
        "ollama": ProviderSpec(
            provider="ollama",
            label="Ollama",
            default_model_id="llama3.2",
            requires_api_key=False,
        ),
    }

    def list_specs(self) -> list[ProviderSpec]:
        return [self._SPECS[name] for name in sorted(self._SPECS)]

    def get_spec(self, provider: str) -> ProviderSpec:
        try:
            return self._SPECS[provider]
        except KeyError as exc:
            raise ValueError(f"Unsupported LLM provider: {provider}") from exc

    def discover_models(
        self,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
    ) -> list[dict[str, object]]:
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
        return results

    def validate_model_config(
        self,
        *,
        provider: str,
        model_id: str,
        api_key_env_var: str | None = None,
        base_url: str | None = None,
        api_version: str | None = None,
    ) -> None:
        result = self.discover_models(
            provider=provider,
            model_id=model_id,
            api_key_env_var=api_key_env_var,
            base_url=base_url,
            api_version=api_version,
        )[0]
        if not result["available"]:
            raise ValueError(str(result["error"]))

        known_models = result["models"]
        if provider in {"openai", "ollama"} and known_models and model_id not in known_models:
            raise ValueError(
                f"Model '{model_id}' is not reported by provider '{provider}'. "
                f"Available models: {', '.join(known_models)}"
            )
