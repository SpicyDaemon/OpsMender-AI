"""Backward-compatible import surface for workflow LLM types.

Sprint 10 introduces ``backend.llm`` as the provider abstraction layer.
This module remains as a compatibility shim for existing imports.
"""

from backend.llm import (
    AnthropicLLM,
    LLM,
    OllamaProvider,
    OpenAIProvider,
    StubLLM,
    create_llm,
    create_provider,
)

__all__ = [
    "AnthropicLLM",
    "LLM",
    "OllamaProvider",
    "OpenAIProvider",
    "StubLLM",
    "create_llm",
    "create_provider",
]
