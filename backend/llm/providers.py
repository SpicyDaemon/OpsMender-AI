"""Provider implementations and adapters."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from typing import Any
import urllib.error
import urllib.request

from .base import LLM, LLMProvider


@dataclasses.dataclass
class ProviderLLMAdapter:
    """Adapt an ``LLMProvider`` to the workflow-facing ``LLM`` interface."""

    provider: LLMProvider

    def invoke(self, prompt: str) -> str:
        return self.provider.complete(prompt)


@dataclasses.dataclass
class StubProvider:
    """Provider for tests and offline mode."""

    response: str = "[stub]"
    echo: bool = False
    model_id: str = "stub"
    calls: list[str] = dataclasses.field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.echo:
            return prompt
        return self.response

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.complete(prompt)

    def list_models(self) -> list[str]:
        return [self.model_id]


class StubLLM(ProviderLLMAdapter):
    """Backward-compatible workflow client for tests and offline mode."""

    def __init__(
        self,
        response: str = "[stub]",
        echo: bool = False,
        model_id: str = "stub",
    ) -> None:
        self.response = response
        self.echo = echo
        self.model_id = model_id
        self.provider = StubProvider(
            response=self.response,
            echo=self.echo,
            model_id=self.model_id,
        )

    @property
    def calls(self) -> list[str]:
        return self.provider.calls


@dataclasses.dataclass
class AnthropicProvider:
    """Provider backed by the Anthropic Messages API."""

    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    api_key_env_var: str = "ANTHROPIC_API_KEY"

    def __post_init__(self) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install it with: uv add anthropic"
            ) from exc

        api_key = os.environ.get(self.api_key_env_var)
        if not api_key:
            raise EnvironmentError(
                f"{self.api_key_env_var} environment variable is not set. "
                "Set it to your Anthropic API key."
            )

        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.complete(prompt)

    def list_models(self) -> list[str]:
        return [self.model]


class AnthropicLLM(ProviderLLMAdapter):
    """Backward-compatible workflow client backed by ``AnthropicProvider``."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.provider = AnthropicProvider(model=self.model, max_tokens=self.max_tokens)


@dataclasses.dataclass
class OpenAIProvider:
    """Provider backed by the OpenAI or Azure OpenAI chat completions API."""

    model: str
    max_tokens: int = 4096
    api_key_env_var: str = "OPENAI_API_KEY"
    base_url: str | None = None
    api_version: str | None = None
    azure: bool = False

    def __post_init__(self) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAIProvider. "
                "Install it with: uv add openai"
            ) from exc

        api_key = os.environ.get(self.api_key_env_var)
        if not api_key:
            raise EnvironmentError(
                f"{self.api_key_env_var} environment variable is not set. "
                "Set it to your OpenAI/Azure OpenAI API key."
            )

        import openai

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if self.azure:
            if not self.base_url:
                raise ValueError("Azure OpenAI requires a base_url")
            if not self.api_version:
                raise ValueError("Azure OpenAI requires an api_version")
            client_kwargs["azure_endpoint"] = self.base_url
            client_kwargs["api_version"] = self.api_version
            self._client = openai.AzureOpenAI(**client_kwargs)
        else:
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**client_kwargs)

    def complete(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content or ""

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.complete(prompt)

    def list_models(self) -> list[str]:
        if self.azure:
            return [self.model]

        # Discovery is on the page-load hot path (/dashboard/models).
        # Cap each call and skip retries so a bad key or slow network
        # fails fast instead of stalling on the SDK's default 10 min
        # timeout x 2 retries.
        models = self._client.with_options(
            timeout=2.0, max_retries=0
        ).models.list()
        data = getattr(models, "data", models)
        ids: list[str] = []
        for item in data:
            model_id = getattr(item, "id", None)
            if model_id:
                ids.append(model_id)
        return ids or [self.model]


@dataclasses.dataclass
class OllamaProvider:
    """Provider backed by the native Ollama HTTP API."""

    model: str = "llama3.2"
    base_url: str = dataclasses.field(
        default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def complete(self, prompt: str) -> str:
        payload = self._post_generate(prompt=prompt, stream=False)
        return payload.get("response", "")

    def stream(self, prompt: str) -> Iterator[str]:
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(self._generate_payload(prompt, stream=True)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("response", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama stream failed: {exc}") from exc

    def list_models(self) -> list[str]:
        request = urllib.request.Request(
            f"{self.base_url}/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            # Hard 2s cap. Discovery runs on every /dashboard/models paint;
            # without this, an unreachable Ollama host stalls the page on
            # the OS TCP connect timeout (~75s on Linux behind a dropping
            # firewall).
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama list_models failed: {exc}") from exc

        models = payload.get("models", [])
        names = [item.get("name") for item in models if item.get("name")]
        return names or [self.model]

    def _post_generate(self, *, prompt: str, stream: bool) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(self._generate_payload(prompt, stream=stream)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama completion failed: {exc}") from exc

    def _generate_payload(self, prompt: str, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
        }
        if self.max_tokens:
            payload["options"] = {"num_predict": self.max_tokens}
        return payload
