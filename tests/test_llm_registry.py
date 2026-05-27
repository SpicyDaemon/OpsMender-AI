"""Tests for provider registry and model-config validation."""

from __future__ import annotations

import pytest

from backend.llm.registry import ProviderRegistry


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """Discovery results are cached for 60s in production; tests must
    start clean so monkeypatched providers actually take effect."""
    ProviderRegistry.clear_discovery_cache()
    yield
    ProviderRegistry.clear_discovery_cache()


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

    def test_discover_models_caches_within_ttl(self, monkeypatch):
        """Sprint 61 follow-up — repeat calls should not hit the live
        provider. /dashboard/models was slow because every page paint
        re-ran the OpenAI + Ollama list calls; the cache short-circuits
        within the 60s TTL."""
        call_count = {"n": 0}

        def _factory(**kwargs):
            call_count["n"] += 1
            return _FakeProvider(["gpt-4o"])

        monkeypatch.setattr("backend.llm.registry.create_provider", _factory)
        registry = ProviderRegistry()

        first = registry.discover_models(provider="openai")
        second = registry.discover_models(provider="openai")

        assert first == second
        assert call_count["n"] == 1, "second call should hit the cache"

        # Different cache key (different provider) should miss.
        registry.discover_models(provider="anthropic")
        assert call_count["n"] == 2

        # use_cache=False forces a fresh discovery.
        registry.discover_models(provider="openai", use_cache=False)
        assert call_count["n"] == 3

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

        with pytest.raises(
            ValueError, match="not currently reported by provider 'openai'"
        ):
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

    def test_discover_models_without_provider_lists_all(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["ok"]),
        )
        registry = ProviderRegistry()

        items = registry.discover_models()

        providers = sorted(item["provider"] for item in items)
        assert providers == ["anthropic", "azure_openai", "ollama", "openai"]
        assert all(item["available"] for item in items)

    def test_validate_model_config_azure_missing_base_url_is_hard_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["deploy-gpt4"]),
        )
        registry = ProviderRegistry()

        with pytest.raises(ValueError, match="base_url"):
            registry.validate_model_config(
                provider="azure_openai",
                model_id="deploy-gpt4",
                api_version="2024-10-21",
                allow_unverified=True,
            )

    def test_validate_model_config_azure_missing_api_version_is_hard_error(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["deploy-gpt4"]),
        )
        registry = ProviderRegistry()

        with pytest.raises(ValueError, match="api_version"):
            registry.validate_model_config(
                provider="azure_openai",
                model_id="deploy-gpt4",
                base_url="https://example-resource.openai.azure.com/",
                allow_unverified=True,
            )

    def test_validate_model_config_anthropic_manual_id_does_not_warn(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["claude-sonnet-4-20250514"]),
        )
        registry = ProviderRegistry()

        result = registry.validate_model_config(
            provider="anthropic",
            model_id="claude-opus-custom",
            allow_unverified=True,
        )

        assert result.warnings == []
        assert result.discovered_models == ["claude-sonnet-4-20250514"]

    def test_validate_model_config_ollama_manual_id_warns(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["llama3.2", "mistral"]),
        )
        registry = ProviderRegistry()

        result = registry.validate_model_config(
            provider="ollama",
            model_id="llama3-custom",
            allow_unverified=True,
        )

        assert len(result.warnings) == 1
        assert result.warnings[0].code == "model_not_reported"

    def test_validate_model_config_ollama_without_api_key(self, monkeypatch):
        captured: dict[str, object] = {}

        def _create_provider(**kwargs):
            captured.update(kwargs)
            return _FakeProvider(["llama3.2"])

        monkeypatch.setattr("backend.llm.registry.create_provider", _create_provider)
        registry = ProviderRegistry()

        result = registry.validate_model_config(
            provider="ollama",
            model_id="llama3.2",
        )

        assert result.warnings == []
        assert captured["provider"] == "ollama"
        assert captured["api_key_env_var"] is None
