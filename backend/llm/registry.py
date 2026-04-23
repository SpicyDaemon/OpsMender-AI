"""Provider registry and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

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
        allow_unverified: bool = False,
    ) -> ValidationResult:
        spec = self.get_spec(provider)

        if spec.requires_base_url and not base_url:
            raise ValueError(f"Provider '{provider}' requires a base_url")
        if spec.requires_api_version and not api_version:
            raise ValueError(f"Provider '{provider}' requires an api_version")

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

        if provider in {"openai", "ollama"} and discovered_models and model_id not in discovered_models:
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
