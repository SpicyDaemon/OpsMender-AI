"""LLM interface for the incident response workflow.

Defines a simple protocol that all workflow nodes use to call an LLM.
This keeps nodes testable (swap in a stub) and supports BYOM in Phase 2.

Phase 1: single hardcoded model (operator sets API key in env).
Phase 2: BYOM — operator picks provider + model in config.

Usage in nodes::

    # Production
    llm = create_llm(provider="anthropic", model_id="claude-sonnet-4-20250514")

    # Testing
    llm = StubLLM(response="fixed answer")
"""

from __future__ import annotations

import dataclasses
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLM(Protocol):
    """Minimal LLM interface — takes a prompt, returns text.

    All workflow nodes call ``llm.invoke(prompt)`` and receive a string.
    This keeps the coupling minimal and makes testing trivial.
    """

    def invoke(self, prompt: str) -> str:
        """Send *prompt* to the model and return the response text."""
        ...


# ---------------------------------------------------------------------------
# Stub implementation (for testing and offline use)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class StubLLM:
    """An LLM that returns a fixed response.  Used in tests and offline mode.

    Parameters
    ----------
    response:
        The fixed string to return for every ``invoke()`` call.
        Defaults to ``"[stub]"`` if not specified.
    echo:
        If ``True``, return the prompt itself as the response
        (useful for testing that the right prompt was sent).
    """

    response: str = "[stub]"
    echo: bool = False

    # Track calls for test assertions
    calls: list[str] = dataclasses.field(default_factory=list)

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.echo:
            return prompt
        return self.response


# ---------------------------------------------------------------------------
# Anthropic implementation (Phase 1 default)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AnthropicLLM:
    """LLM backed by the Anthropic Messages API.

    Requires the ``anthropic`` package and ``ANTHROPIC_API_KEY`` env var.

    Parameters
    ----------
    model:
        Model ID to use (default: ``claude-sonnet-4-20250514``).
    max_tokens:
        Maximum tokens in the response.
    """

    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        try:
            import anthropic  # noqa: F811
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicLLM. "
                "Install it with: uv add anthropic"
            ) from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Set it to your Anthropic API key."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def invoke(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
