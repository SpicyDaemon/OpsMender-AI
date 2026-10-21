"""Tests for provider registry and model-config validation."""

from __future__ import annotations

import sys
import types

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
            "deepseek",
            "kimi",
            "ollama",
            "openai",
            "openai_compatible",
            "qwen",
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
        """Sprint 61 follow-up - repeat calls should not hit the live
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
            "deepseek",
            "kimi",
            "ollama",
            "openai",
            "openai_compatible",
            "qwen",
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


# Qwen, DeepSeek and Kimi: vendors that serve the OpenAI API at their own
# hosts. The expected values are written out here, not read from the specs, so
# a changed host, key variable or default URL fails a test.
NAMED_VENDORS = {
    "qwen": {
        "label": "Qwen",
        "key": "DASHSCOPE_API_KEY",
        "hosts": (
            "dashscope-intl.aliyuncs.com",
            "dashscope.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "cn-hongkong.dashscope.aliyuncs.com",
            ".maas.aliyuncs.com",
        ),
        "default_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "other_url": "https://ws-1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    },
    "deepseek": {
        "label": "DeepSeek",
        "key": "DEEPSEEK_API_KEY",
        "hosts": ("api.deepseek.com",),
        "default_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "other_url": "https://api.deepseek.com/v1",
    },
    "kimi": {
        "label": "Kimi",
        "key": "MOONSHOT_API_KEY",
        "hosts": ("api.moonshot.ai", "api.moonshot.cn"),
        "default_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k3",
        "other_url": "https://api.moonshot.cn/v1",
    },
}


def _install_recording_openai(monkeypatch, models=("listed-model",)):
    """Replace the openai SDK with a client that records how it was built
    and lists ``models``; nothing leaves the process."""
    built: list[dict[str, object]] = []

    class _Client:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.models = types.SimpleNamespace(
                list=lambda: types.SimpleNamespace(
                    data=[types.SimpleNamespace(id=m) for m in models]
                )
            )

        def with_options(self, **_kwargs):
            return self

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_Client))
    return built


@pytest.mark.parametrize("provider", sorted(NAMED_VENDORS))
class TestNamedOpenAICompatibleVendors:
    def test_spec(self, provider):
        want = NAMED_VENDORS[provider]
        spec = ProviderRegistry().get_spec(provider)

        assert spec.label == want["label"]
        assert spec.default_api_key_env_var == want["key"]
        assert spec.allowed_hosts == want["hosts"]
        assert spec.default_base_url == want["default_url"]
        assert spec.default_model_id == want["model"]
        assert spec.requires_api_key is True
        assert spec.requires_base_url is False

    def test_refuses_a_foreign_host(self, provider, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["m"]),
        )
        hosts = ", ".join(NAMED_VENDORS[provider]["hosts"])
        for foreign in (
            "https://127.0.0.1:9/v1",
            "https://api.openai.com/v1",
            "https://api.deepseek.com.example.net/v1",
        ):
            with pytest.raises(ValueError) as refused:
                ProviderRegistry().validate_model_config(
                    provider=provider,
                    model_id="m",
                    base_url=foreign,
                    allow_unverified=True,
                )
            assert str(refused.value) == (
                f"Provider '{provider}' only connects to {hosts}; "
                "base_url must be a URL there"
            )

    def test_refuses_http_on_its_own_host(self, provider, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["m"]),
        )
        plain = NAMED_VENDORS[provider]["default_url"].replace("https://", "http://")
        with pytest.raises(ValueError) as refused:
            ProviderRegistry().validate_model_config(
                provider=provider, model_id="m", base_url=plain, allow_unverified=True
            )
        assert str(refused.value) == (
            f"Provider '{provider}' only connects over https; "
            "base_url must start with https://"
        )

    def test_accepts_another_of_its_hosts(self, provider, monkeypatch):
        captured: dict[str, object] = {}

        def _create(**kwargs):
            captured.update(kwargs)
            return _FakeProvider(["m"])

        monkeypatch.setattr("backend.llm.registry.create_provider", _create)
        other = NAMED_VENDORS[provider]["other_url"]
        ProviderRegistry().validate_model_config(
            provider=provider, model_id="m", base_url=other
        )
        assert captured["base_url"] == other

    def test_no_base_url_connects_to_the_vendor_default(self, provider, monkeypatch):
        """Saving without a base URL is valid: the factory applies the
        vendor's URL and the provider's own key variable."""
        want = NAMED_VENDORS[provider]
        built = _install_recording_openai(monkeypatch, models=(want["model"],))
        monkeypatch.setenv(want["key"], "test-key")

        result = ProviderRegistry().validate_model_config(
            provider=provider, model_id=want["model"]
        )

        assert result.warnings == []
        assert result.discovered_models == [want["model"]]
        assert built == [{"api_key": "test-key", "base_url": want["default_url"]}]

    def test_a_model_the_vendor_does_not_list_is_refused(self, provider, monkeypatch):
        monkeypatch.setattr(
            "backend.llm.registry.create_provider",
            lambda **kwargs: _FakeProvider(["listed-model"]),
        )
        registry = ProviderRegistry()
        with pytest.raises(
            ValueError, match=f"not currently reported by provider '{provider}'"
        ):
            registry.validate_model_config(provider=provider, model_id="typo-model")

        allowed = registry.validate_model_config(
            provider=provider, model_id="typo-model", allow_unverified=True
        )
        assert [w.code for w in allowed.warnings] == ["model_not_reported"]

    def test_placeholder_key_is_unavailable_without_a_call(self, provider, monkeypatch):
        key = NAMED_VENDORS[provider]["key"]
        built = _install_recording_openai(monkeypatch)
        monkeypatch.setenv(key, "your_key_here")

        [item] = ProviderRegistry().discover_models(provider=provider)

        assert item["available"] is False
        assert item["error"].startswith(f"{key} is set to the .env.example placeholder")
        assert built == []


def test_http_is_refused_on_every_fixed_host(monkeypatch):
    """The https rule covers the fixed-host providers that came before, and
    the free-URL providers keep plain http for local runtimes."""
    monkeypatch.setattr(
        "backend.llm.registry.create_provider",
        lambda **kwargs: _FakeProvider(["m"]),
    )
    registry = ProviderRegistry()
    for provider, url, version in (
        ("openai", "http://api.openai.com/v1", None),
        ("anthropic", "http://api.anthropic.com", None),
        ("azure_openai", "http://example-resource.openai.azure.com/", "2024-10-21"),
    ):
        with pytest.raises(ValueError, match="only connects over https"):
            registry.validate_model_config(
                provider=provider, model_id="m", base_url=url, api_version=version
            )

    registry.validate_model_config(
        provider="openai_compatible",
        model_id="m",
        base_url="http://localhost:1234/v1",
        allow_unverified=True,
    )
    registry.validate_model_config(
        provider="ollama",
        model_id="m",
        base_url="http://localhost:11434",
        allow_unverified=True,
    )
