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
