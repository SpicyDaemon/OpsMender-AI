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
        assert providers == [
            "anthropic",
            "azure_openai",
            "bedrock",
            "ollama",
            "openai",
            "openai_compatible",
            "vertex_ai",
        ]

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

    def test_openai_compatible_spec_is_registered(self):
        registry = ProviderRegistry()
        providers = [spec.provider for spec in registry.list_specs()]
        assert "openai_compatible" in providers

        spec = registry.get_spec("openai_compatible")
        assert spec.requires_base_url is True
        assert spec.requires_api_key is False
        assert spec.default_api_key_env_var is None

    def test_bedrock_spec_is_registered(self):
        registry = ProviderRegistry()
        spec = registry.get_spec("bedrock")
        assert spec.label == "AWS Bedrock"
        assert spec.requires_api_key is False
        assert spec.default_model_id == "anthropic.claude-sonnet-4-6"

    def test_validate_bedrock_requires_region(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="provider_meta.region"):
            registry.validate_model_config(
                provider="bedrock",
                model_id="anthropic.claude-sonnet-4-6",
            )

    def test_validate_bedrock_accepts_region_and_optional_profile(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["anthropic.claude-sonnet-4-6"]),
        )
        registry = ProviderRegistry()
        result = registry.validate_model_config(
            provider="bedrock",
            model_id="anthropic.claude-sonnet-4-6",
            provider_meta={"region": "us-east-1", "profile": "prod"},
        )
        assert result.warnings == []
        assert result.discovered_models == ["anthropic.claude-sonnet-4-6"]

    def test_vertex_ai_spec_is_registered(self):
        registry = ProviderRegistry()
        spec = registry.get_spec("vertex_ai")
        assert spec.label == "GCP Vertex AI"
        assert spec.requires_api_key is False
        assert spec.default_model_id == "google/gemini-2.5-flash"

    def test_validate_vertex_ai_requires_project(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="provider_meta.project"):
            registry.validate_model_config(
                provider="vertex_ai",
                model_id="google/gemini-2.5-flash",
                provider_meta={"location": "us-central1"},
            )

    def test_validate_vertex_ai_requires_location(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="provider_meta.location"):
            registry.validate_model_config(
                provider="vertex_ai",
                model_id="google/gemini-2.5-flash",
                provider_meta={"project": "opsmender-prod"},
            )

    def test_validate_vertex_ai_accepts_project_and_location(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["google/gemini-2.5-flash"]),
        )
        registry = ProviderRegistry()
        result = registry.validate_model_config(
            provider="vertex_ai",
            model_id="google/gemini-2.5-flash",
            provider_meta={"project": "opsmender-prod", "location": "us-central1"},
        )
        assert result.warnings == []
        assert result.discovered_models == ["google/gemini-2.5-flash"]

    def test_validate_openai_compatible_requires_base_url(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="base_url"):
            registry.validate_model_config(
                provider="openai_compatible",
                model_id="local-llama",
            )

    def test_validate_openai_compatible_allows_no_api_key(self, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["gpt-4o"]),
        )
        registry = ProviderRegistry()
        # api_key_env_var omitted → still valid because requires_api_key=False.
        result = registry.validate_model_config(
            provider="openai_compatible",
            model_id="gpt-4o",
            base_url="http://localhost:1234/v1",
        )
        assert result.discovered_models == ["gpt-4o"]
        assert result.warnings == []

    def test_validate_openai_compatible_unknown_model_warns_when_allowed(
        self, monkeypatch
    ):
        # The endpoint reports a specific catalog; a manual model id that
        # isn't in it should be allowed under allow_unverified=True with
        # a warning (sprint acceptance criterion: manual entry works).
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["gpt-4o", "gpt-4o-mini"]),
        )
        registry = ProviderRegistry()
        result = registry.validate_model_config(
            provider="openai_compatible",
            model_id="anthropic/claude-3.5-sonnet",
            base_url="https://openrouter.ai/api/v1",
            allow_unverified=True,
        )
        assert any(w.code == "model_not_reported" for w in result.warnings)

    def test_discover_cache_ttl_overrides_for_cloud_providers(self, monkeypatch):
        from backend.llm import registry as registry_mod

        # Sprint 62 plan: cloud catalogs change rarely → 1h TTL. Local /
        # openai_compatible keep the default 60s. Confirm the lookup
        # helper returns the right values without instantiating any
        # cloud SDKs.
        assert registry_mod._ttl_for("bedrock") == 3600.0
        assert registry_mod._ttl_for("vertex_ai") == 3600.0
        assert registry_mod._ttl_for("oci_genai") == 3600.0
        assert registry_mod._ttl_for("openai_compatible") == 60.0
        assert registry_mod._ttl_for("ollama") == 60.0
        assert registry_mod._ttl_for(None) == 60.0

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
        assert providers == [
            "anthropic",
            "azure_openai",
            "bedrock",
            "ollama",
            "openai",
            "openai_compatible",
            "vertex_ai",
        ]
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


class TestPlaceholderDetection:
    """Discovery must not show a misleading green dot for unfilled .env vars."""

    def test_looks_like_placeholder_catches_shipped_templates(self):
        from backend.llm.registry import _looks_like_placeholder

        # Real values are NOT placeholders
        assert _looks_like_placeholder("sk-ant-abc123") is False
        assert _looks_like_placeholder("https://opsmender.azure.com") is False

        # Shipped .env.example placeholders
        assert _looks_like_placeholder("your_anthropic_key_here") is True
        assert _looks_like_placeholder("your_openai_key_here") is True
        assert _looks_like_placeholder("your_azure_key_here") is True
        assert _looks_like_placeholder("your_deployment_name") is True
        assert _looks_like_placeholder("https://your-resource.openai.azure.com") is True

        # Edge cases
        assert _looks_like_placeholder("") is True
        assert _looks_like_placeholder(None) is True
        assert _looks_like_placeholder("  ") is True
        assert _looks_like_placeholder("change-me-in-production") is True

    def test_discovery_marks_anthropic_unavailable_when_key_is_placeholder(
        self, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "your_anthropic_key_here")
        ProviderRegistry.clear_discovery_cache()
        registry = ProviderRegistry()

        results = registry.discover_models(provider="anthropic")

        assert len(results) == 1
        assert results[0]["available"] is False
        assert "placeholder" in results[0]["error"].lower()

    def test_discovery_marks_anthropic_available_with_real_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")

        def _create_provider(**kwargs):
            return _FakeProvider(["claude-sonnet-4"])

        monkeypatch.setattr("backend.llm.registry.create_provider", _create_provider)
        ProviderRegistry.clear_discovery_cache()
        registry = ProviderRegistry()

        results = registry.discover_models(provider="anthropic")

        assert len(results) == 1
        assert results[0]["available"] is True

    def test_discovery_marks_azure_unavailable_when_endpoint_is_placeholder(
        self, monkeypatch
    ):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "real-azure-key")
        monkeypatch.setenv(
            "AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"
        )
        ProviderRegistry.clear_discovery_cache()
        registry = ProviderRegistry()

        results = registry.discover_models(provider="azure_openai")

        assert len(results) == 1
        assert results[0]["available"] is False
        assert "azure" in results[0]["error"].lower()
