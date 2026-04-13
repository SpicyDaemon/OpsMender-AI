"""Core protocols for LLM providers and workflow-facing LLM clients."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class LLM(Protocol):
    """Minimal interface consumed by workflow nodes."""

    def invoke(self, prompt: str) -> str:
        """Send *prompt* to a model and return the response text."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Provider abstraction for Sprint 10 BYOM support."""

    def complete(self, prompt: str) -> str:
        """Return a single completion for *prompt*."""
        ...

    def stream(self, prompt: str) -> Iterator[str]:
        """Yield streamed chunks for *prompt*."""
        ...

    def list_models(self) -> list[str]:
        """Return the provider's available or configured model IDs."""
        ...
