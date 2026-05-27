"""Tests for the Sprint 10 provider abstraction layer."""

from __future__ import annotations

import sys
import types
import builtins

import pytest

from backend.agent.llm import AnthropicLLM, LLM, StubLLM, create_llm, create_provider
from backend.llm import (
    AnthropicProvider,
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderLLMAdapter,
    StubProvider,
)


class TestStubProvider:
    def test_complete_returns_fixed_response(self):
        provider = StubProvider(response="hello")
        assert provider.complete("prompt") == "hello"

    def test_complete_echo_mode(self):
        provider = StubProvider(echo=True)
        assert provider.complete("echo this") == "echo this"

    def test_stream_yields_completion(self):
        provider = StubProvider(response="chunk")
        assert list(provider.stream("prompt")) == ["chunk"]

    def test_tracks_calls(self):
        provider = StubProvider()
        provider.complete("one")
        provider.complete("two")
        assert provider.calls == ["one", "two"]

    def test_lists_models(self):
        provider = StubProvider(model_id="stub-model")
        assert provider.list_models() == ["stub-model"]

    def test_satisfies_protocol(self):
        assert isinstance(StubProvider(), LLMProvider)


class TestProviderLLMAdapter:
    def test_adapter_uses_provider_complete(self):
        llm = ProviderLLMAdapter(StubProvider(response="adapter"))
        assert llm.invoke("prompt") == "adapter"
        assert isinstance(llm, LLM)


class TestFactory:
    def test_create_stub_provider(self):
        provider = create_provider(provider="stub", response="ok", model_id="local")
        assert isinstance(provider, StubProvider)
        assert provider.complete("x") == "ok"
        assert provider.list_models() == ["local"]

    def test_create_stub_llm(self):
        llm = create_llm(provider="stub", response="ok")
        assert llm.invoke("x") == "ok"
        assert isinstance(llm, LLM)

    def test_create_openai_provider(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        provider = create_provider(provider="openai", model_id="gpt-4o-mini")
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o-mini"

    def test_create_azure_openai_provider(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        provider = create_provider(
            provider="azure_openai",
            model_id="deploy-gpt4",
            base_url="https://example-resource.openai.azure.com/",
            api_version="2024-10-21",
        )
        assert isinstance(provider, OpenAIProvider)
        assert provider.azure is True

    def test_create_ollama_provider(self):
        provider = create_provider(
            provider="ollama",
            model_id="llama3.1",
            base_url="http://localhost:11434",
        )
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "llama3.1"

    def test_unsupported_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_provider(provider="unknown")


class TestBackwardCompatibility:
    def test_stub_llm_is_backed_by_provider(self):
        llm = StubLLM(response="hello")
        assert llm.invoke("prompt") == "hello"
        assert llm.calls == ["prompt"]
        assert isinstance(llm.provider, StubProvider)

    def test_anthropic_llm_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            AnthropicLLM()

    def test_anthropic_provider_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider()

    def test_anthropic_provider_respects_custom_api_key_env_var(self, monkeypatch):
        fake_module = types.SimpleNamespace(
            Anthropic=lambda **kwargs: types.SimpleNamespace(kwargs=kwargs)
        )
        monkeypatch.setitem(sys.modules, "anthropic", fake_module)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPSMENDER_ANTHROPIC_KEY", "test-key")

        provider = AnthropicProvider(api_key_env_var="OPSMENDER_ANTHROPIC_KEY")

        assert provider._client.kwargs["api_key"] == "test-key"


def _install_fake_openai(monkeypatch):
    class _FakeModel:
        def __init__(self, model_id):
            self.id = model_id

    class _FakeModels:
        def list(self):
            return types.SimpleNamespace(
                data=[_FakeModel("gpt-4o"), _FakeModel("gpt-4o-mini")]
            )

    class _FakeCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(
                            content=f"reply:{kwargs['model']}"
                        )
                    )
                ]
            )

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = _FakeChat()
            self.models = _FakeModels()

        def with_options(self, **_kwargs):
            # Sprint 61 follow-up: list_models calls .with_options(timeout=...,
            # max_retries=0) so a slow OpenAI endpoint doesn't stall the
            # /dashboard/models page. The fake returns itself so the
            # downstream .models.list() still resolves.
            return self

    fake_module = types.SimpleNamespace(
        OpenAI=_FakeOpenAIClient,
        AzureOpenAI=_FakeOpenAIClient,
    )
    monkeypatch.setitem(sys.modules, "openai", fake_module)


class TestOpenAIProvider:
    def test_missing_package_raises(self, monkeypatch):
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with pytest.raises(ImportError, match="openai"):
            OpenAIProvider(model="gpt-4o")

    def test_missing_api_key_raises(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
            OpenAIProvider(model="gpt-4o")

    def test_complete_standard_openai(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        provider = OpenAIProvider(model="gpt-4o", base_url="http://localhost:11434/v1")
        assert provider.complete("hello") == "reply:gpt-4o"

    def test_list_models_standard_openai(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        provider = OpenAIProvider(model="gpt-4o")
        assert provider.list_models() == ["gpt-4o", "gpt-4o-mini"]

    def test_azure_requires_base_url(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        with pytest.raises(ValueError, match="base_url"):
            OpenAIProvider(
                model="deploy-gpt4",
                api_key_env_var="AZURE_OPENAI_API_KEY",
                azure=True,
                api_version="2024-10-21",
            )

    def test_azure_requires_api_version(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        with pytest.raises(ValueError, match="api_version"):
            OpenAIProvider(
                model="deploy-gpt4",
                api_key_env_var="AZURE_OPENAI_API_KEY",
                azure=True,
                base_url="https://example-resource.openai.azure.com/",
            )

    def test_complete_azure_openai(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        provider = OpenAIProvider(
            model="deploy-gpt4",
            api_key_env_var="AZURE_OPENAI_API_KEY",
            azure=True,
            base_url="https://example-resource.openai.azure.com/",
            api_version="2024-10-21",
        )
        assert provider.complete("hello") == "reply:deploy-gpt4"
        assert provider.list_models() == ["deploy-gpt4"]


class TestOpenAICompatibleProvider:
    """Sprint 62 Step 1 — generic OpenAI-API-compatible endpoint."""

    def test_requires_base_url(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        with pytest.raises(ValueError, match="base_url"):
            OpenAICompatibleProvider(model="gpt-4o", base_url="")

    def test_works_without_api_key(self, monkeypatch):
        # Local endpoints (LM Studio, vLLM) often have no auth at all.
        _install_fake_openai(monkeypatch)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = OpenAICompatibleProvider(
            model="local-llama",
            base_url="http://localhost:1234/v1",
        )
        assert provider.complete("hello") == "reply:local-llama"

    def test_uses_api_key_when_env_var_configured(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        provider = OpenAICompatibleProvider(
            model="anthropic/claude-3.5-sonnet",
            base_url="https://openrouter.ai/api/v1",
            api_key_env_var="OPENROUTER_API_KEY",
        )
        assert provider.complete("hi") == "reply:anthropic/claude-3.5-sonnet"

    def test_missing_api_key_env_var_raises_when_configured(self, monkeypatch):
        # If the operator names an env var, we treat it as required —
        # silently sending no auth would surprise them.
        _install_fake_openai(monkeypatch)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY"):
            OpenAICompatibleProvider(
                model="x",
                base_url="https://openrouter.ai/api/v1",
                api_key_env_var="OPENROUTER_API_KEY",
            )

    def test_list_models_returns_endpoint_catalog(self, monkeypatch):
        _install_fake_openai(monkeypatch)
        provider = OpenAICompatibleProvider(
            model="gpt-4o",
            base_url="http://localhost:1234/v1",
        )
        assert provider.list_models() == ["gpt-4o", "gpt-4o-mini"]

    def test_list_models_falls_back_to_configured_model_on_error(self, monkeypatch):
        # Local endpoints often don't implement /v1/models. The sprint
        # acceptance criterion says manual model entry must keep working
        # — so list_models must gracefully fall back instead of raising.
        _install_fake_openai(monkeypatch)
        provider = OpenAICompatibleProvider(
            model="my-custom-model",
            base_url="http://localhost:1234/v1",
        )

        def _raise():
            raise RuntimeError("404 Not Found")

        # Patch the underlying client's models.list to raise. The fake
        # ``_FakeOpenAIClient.with_options`` returns self, so this
        # patch is what the discovery path sees.
        provider._client.models.list = _raise  # type: ignore[assignment]
        assert provider.list_models() == ["my-custom-model"]


class _FakeURLResponse:
    def __init__(self, body: bytes, *, lines: list[bytes] | None = None):
        self._body = body
        self._lines = lines or body.splitlines(keepends=True)

    def read(self) -> bytes:
        return self._body

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestOllamaProvider:
    def test_complete_calls_native_generate_api(self, monkeypatch):
        seen = {}

        def _fake_urlopen(request):
            seen["url"] = request.full_url
            seen["body"] = request.data.decode("utf-8")
            return _FakeURLResponse(b'{"response":"local-reply","done":true}')

        monkeypatch.setattr(
            "backend.llm.providers.urllib.request.urlopen", _fake_urlopen
        )
        provider = OllamaProvider(model="llama3.2", base_url="http://localhost:11434")
        assert provider.complete("hello") == "local-reply"
        assert seen["url"] == "http://localhost:11434/api/generate"
        assert '"model": "llama3.2"' in seen["body"]
        assert '"prompt": "hello"' in seen["body"]

    def test_stream_yields_chunks(self, monkeypatch):
        lines = [
            b'{"response":"hello ","done":false}\n',
            b'{"response":"world","done":false}\n',
            b'{"response":"","done":true}\n',
        ]

        def _fake_urlopen(request):
            return _FakeURLResponse(b"", lines=lines)

        monkeypatch.setattr(
            "backend.llm.providers.urllib.request.urlopen", _fake_urlopen
        )
        provider = OllamaProvider(base_url="http://localhost:11434")
        assert list(provider.stream("test")) == ["hello ", "world"]

    def test_list_models_uses_tags_api(self, monkeypatch):
        # Sprint 61 follow-up: list_models now passes timeout=2.0 to urlopen
        # so an unreachable Ollama host can't stall the page paint. Fake
        # accepts and ignores the kwarg.
        def _fake_urlopen(request, timeout=None):
            assert request.full_url == "http://localhost:11434/api/tags"
            return _FakeURLResponse(
                b'{"models":[{"name":"llama3.2"},{"name":"mistral"}]}'
            )

        monkeypatch.setattr(
            "backend.llm.providers.urllib.request.urlopen", _fake_urlopen
        )
        provider = OllamaProvider(base_url="http://localhost:11434")
        assert provider.list_models() == ["llama3.2", "mistral"]

    def test_network_failure_raises_runtime_error(self, monkeypatch):
        def _fake_urlopen(request):
            raise OSError("connection refused")

        monkeypatch.setattr(
            "backend.llm.providers.urllib.request.urlopen", _fake_urlopen
        )
        provider = OllamaProvider(base_url="http://localhost:11434")
        with pytest.raises(RuntimeError, match="Ollama completion failed"):
            provider.complete("hello")
