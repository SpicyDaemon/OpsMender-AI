"""Tests for provider registry and model-config validation."""

from __future__ import annotations

import pytest

from backend.llm.registry import ProviderRegistry


class _FakeProvider:
    def __init__(self, models):
        self._models = models

    def list_models(self):
        return self._models


class TestProviderRegistry:
    def test_list_specs_contains_supported_providers(self):
        registry = ProviderRegistry()

        providers = [spec.provider for spec in registry.list_specs()]
        assert providers == ["anthropic", "azure_openai", "ollama", "openai"]

    def test_discover_models_returns_available_provider(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["gpt-4o", "gpt-4o-mini"]),
        )
        registry = ProviderRegistry()

        items = registry.discover_models(provider="openai")

        assert len(items) == 1
        assert items[0]["provider"] == "openai"
        assert items[0]["available"] is True
        assert items[0]["models"] == ["gpt-4o", "gpt-4o-mini"]

    def test_discover_models_captures_provider_errors(self, monkeypatch):
        def _raise(**kwargs):
            raise EnvironmentError("missing key")

        monkeypatch.setattr("backend.llm.registry.create_provider", _raise)
        registry = ProviderRegistry()

        items = registry.discover_models(provider="anthropic")

        assert len(items) == 1
        assert items[0]["provider"] == "anthropic"
        assert items[0]["available"] is False
        assert "missing key" in items[0]["error"]

    def test_validate_model_config_rejects_unknown_provider(self):
        registry = ProviderRegistry()

        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            registry.validate_model_config(provider="hf", model_id="mistral")

    def test_validate_model_config_rejects_unlisted_openai_model(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["gpt-4o", "gpt-4o-mini"]),
        )
        registry = ProviderRegistry()

        with pytest.raises(ValueError, match="not currently reported by provider 'openai'"):
            registry.validate_model_config(provider="openai", model_id="gpt-5")

    def test_validate_model_config_allows_azure_deployment_name(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["other-deployment"]),
        )
        registry = ProviderRegistry()

        registry.validate_model_config(
            provider="azure_openai",
            model_id="my-deployment",
            base_url="https://example-resource.openai.azure.com/",
            api_version="2024-10-21",
        )

    def test_validate_model_config_returns_warning_when_provider_unverified(
        self, monkeypatch
    ):
        def _raise(**kwargs):
            raise EnvironmentError("missing key")

        monkeypatch.setattr("backend.llm.registry.create_provider", _raise)
        registry = ProviderRegistry()

        result = registry.validate_model_config(
            provider="openai",
            model_id="gpt-4o",
            allow_unverified=True,
        )

        assert len(result.warnings) == 1
        assert result.warnings[0].code == "provider_unverified"
        assert "missing key" in result.warnings[0].message

    def test_validate_model_config_returns_warning_for_manual_model_id(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["gpt-4o", "gpt-4o-mini"]),
        )
        registry = ProviderRegistry()

        result = registry.validate_model_config(
            provider="openai",
            model_id="gpt-5-custom",
            allow_unverified=True,
        )

        assert len(result.warnings) == 1
        assert result.warnings[0].code == "model_not_reported"
        assert "Saving anyway" in result.warnings[0].message
