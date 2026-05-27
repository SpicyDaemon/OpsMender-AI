"""Provider implementations and adapters."""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Iterator
from typing import Any
import urllib.error
import urllib.parse
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
class OpenAICompatibleProvider:
    """Provider for any OpenAI-API-compatible endpoint.

    Sprint 62 Step 1 — covers vLLM, LM Studio, OpenRouter, Together,
    Groq, Fireworks, Anyscale, and most local OpenAI-shape runtimes
    with one provider shape. The OpenAI SDK is reused; only the
    construction constraints differ:

      * ``base_url`` is required (this is what makes the endpoint
        "custom" — without it the operator should use the plain
        ``openai`` provider).
      * ``api_key_env_var`` is optional. Some local endpoints
        (vLLM behind a private network, LM Studio's default
        ``http://localhost:1234/v1``) accept any string or no key at
        all; we send a placeholder when none is configured so the SDK
        doesn't raise.
      * ``list_models`` falls back to ``[self.model]`` if the endpoint
        does not implement ``/v1/models`` — manual model entry must
        keep working per the sprint acceptance criteria.
    """

    model: str
    base_url: str
    max_tokens: int = 4096
    api_key_env_var: str | None = None

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("OpenAI-compatible provider requires a base_url")

        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAICompatibleProvider. "
                "Install it with: uv add openai"
            ) from exc

        api_key: str | None = None
        if self.api_key_env_var:
            api_key = os.environ.get(self.api_key_env_var)
            if not api_key:
                raise EnvironmentError(
                    f"{self.api_key_env_var} environment variable is not set. "
                    "Either set it to the endpoint's API key, or clear the "
                    "api_key_env_var field if the endpoint does not require one."
                )

        import openai

        # The OpenAI SDK requires _some_ truthy api_key string even for
        # keyless endpoints. Send a placeholder when the operator hasn't
        # configured one — local runtimes ignore it.
        self._client = openai.OpenAI(
            api_key=api_key or "no-key",
            base_url=self.base_url,
        )

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
        # 2s discovery cap matches the OpenAI provider — discovery is on
        # the page-load hot path. If the endpoint doesn't implement
        # /v1/models, fall back to the configured model so manual entry
        # keeps working.
        try:
            models = self._client.with_options(
                timeout=2.0, max_retries=0
            ).models.list()
        except Exception:
            return [self.model]
        data = getattr(models, "data", models)
        ids: list[str] = []
        for item in data:
            model_id = getattr(item, "id", None)
            if model_id:
                ids.append(model_id)
        return ids or [self.model]


@dataclasses.dataclass
class BedrockProvider:
    """Provider backed by Amazon Bedrock via boto3.

    Uses the native AWS credential chain through ``boto3.Session``:
    env vars, shared credentials/config, IAM role, or ECS/EKS task role.
    Operators only need to supply a region plus an optional shared-config
    profile name.
    """

    model: str = "anthropic.claude-sonnet-4-6"
    region: str = ""
    profile: str | None = None
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.region:
            raise ValueError("Bedrock provider requires a region")

        try:
            import boto3  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'boto3' package is required for BedrockProvider. "
                "Install it with: uv add boto3"
            ) from exc

        import boto3

        session_kwargs: dict[str, Any] = {"region_name": self.region}
        if self.profile:
            session_kwargs["profile_name"] = self.profile

        session = boto3.Session(**session_kwargs)
        self._control_client = session.client("bedrock", region_name=self.region)
        self._runtime_client = session.client(
            "bedrock-runtime", region_name=self.region
        )

    def complete(self, prompt: str) -> str:
        response = self._runtime_client.converse(
            modelId=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            inferenceConfig={"maxTokens": self.max_tokens},
        )
        content = (
            response.get("output", {})
            .get("message", {})
            .get("content", [])
        )
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "".join(text_parts).strip()

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.complete(prompt)

    def list_models(self) -> list[str]:
        response = self._control_client.list_foundation_models(
            byOutputModality="TEXT"
        )
        ids: list[str] = []
        for summary in response.get("modelSummaries", []):
            if not isinstance(summary, dict):
                continue
            lifecycle = summary.get("modelLifecycle") or {}
            if isinstance(lifecycle, dict) and lifecycle.get("status") == "LEGACY":
                continue
            model_id = summary.get("modelId")
            if isinstance(model_id, str) and model_id:
                ids.append(model_id)
        return sorted(set(ids)) or [self.model]


@dataclasses.dataclass
class VertexAIProvider:
    """Provider backed by Vertex AI publisher models.

    Uses ADC for credentials and stores only non-secret routing metadata
    (project + location) in the saved model config.
    """

    model: str = "google/gemini-2.5-flash"
    project: str = ""
    location: str = ""
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.project:
            raise ValueError("Vertex AI provider requires a project")
        if not self.location:
            raise ValueError("Vertex AI provider requires a location")

        try:
            import vertexai  # noqa: F401
            import google.auth  # noqa: F401
            from google.auth.transport.requests import AuthorizedSession  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "The 'google-cloud-aiplatform' package is required for VertexAIProvider. "
                "Install it with: uv add google-cloud-aiplatform"
            ) from exc

        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        import vertexai

        vertexai.init(project=self.project, location=self.location)
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self._session = AuthorizedSession(credentials)
        self._base_url = f"https://{self.location}-aiplatform.googleapis.com"

    def _publisher_and_model(self) -> tuple[str, str]:
        if self.model.startswith("projects/"):
            parts = self.model.split("/")
            try:
                publisher = parts[5]
                model_id = parts[7]
                return publisher, model_id
            except IndexError as exc:
                raise ValueError(f"Unsupported Vertex AI model path: {self.model}") from exc
        if self.model.startswith("publishers/"):
            parts = self.model.split("/")
            try:
                return parts[1], parts[3]
            except IndexError as exc:
                raise ValueError(f"Unsupported Vertex AI model path: {self.model}") from exc
        if "/" in self.model:
            publisher, model_id = self.model.split("/", 1)
            return publisher, model_id
        lowered = self.model.lower()
        if lowered.startswith("gemini"):
            return "google", self.model
        if lowered.startswith("claude"):
            return "anthropic", self.model
        if lowered.startswith("llama"):
            return "meta", self.model
        return "google", self.model

    def _qualified_model_name(self) -> str:
        if self.model.startswith("projects/"):
            return self.model
        if self.model.startswith("publishers/"):
            return (
                f"projects/{self.project}/locations/{self.location}/{self.model}"
            )
        publisher, model_id = self._publisher_and_model()
        return (
            "projects/"
            f"{self.project}/locations/{self.location}/publishers/{publisher}/models/{model_id}"
        )

    def complete(self, prompt: str) -> str:
        model_name = self._qualified_model_name()
        encoded_model = urllib.parse.quote(model_name, safe="/")
        response = self._session.post(
            f"{self._base_url}/v1/{encoded_model}:generateContent",
            json={
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {"maxOutputTokens": self.max_tokens},
            },
            timeout=30,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            return ""
        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )
        text_parts = [
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "".join(text_parts).strip()

    def stream(self, prompt: str) -> Iterator[str]:
        yield self.complete(prompt)

    def list_models(self) -> list[str]:
        ids: set[str] = set()
        for publisher in ("google", "anthropic", "meta"):
            response = self._session.get(
                f"{self._base_url}/v1beta1/publishers/{publisher}/models",
                params={
                    "listAllVersions": "true",
                    "view": "PUBLISHER_MODEL_VIEW_BASIC",
                },
                timeout=15,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            payload = response.json()
            for model in payload.get("publisherModels", []):
                if not isinstance(model, dict):
                    continue
                name = model.get("name")
                if not isinstance(name, str):
                    continue
                parts = name.split("/")
                if len(parts) >= 4:
                    ids.add(f"{publisher}/{parts[-1]}")
        return sorted(ids) or [self.model]


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
