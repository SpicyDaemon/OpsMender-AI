"""Memory tag normalization helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SEVERITY_LEVELS = frozenset({"critical", "high", "medium", "low"})


def canonicalize_memory_tag(tag: str) -> str:
    normalised = tag.strip().lower()
    if normalised in SEVERITY_LEVELS:
        return f"severity-{normalised}"
    return normalised


def normalize_memory_tags(
    raw_tags: Iterable[Any] | None,
    *,
    limit: int | None = None,
) -> list[str]:
    tags: list[str] = []
    for tag in raw_tags or []:
        if not isinstance(tag, str):
            continue
        normalised = canonicalize_memory_tag(tag)
        if normalised and normalised not in tags:
            tags.append(normalised)
        if limit is not None and len(tags) >= limit:
            break
    return tags
