"""Analyzer registry — discoverable list of built-in + plugin analyzers."""

from __future__ import annotations

import dataclasses

from backend.auditor.base import Analyzer


@dataclasses.dataclass(slots=True)
class AnalyzerSpec:
    key: str
    label: str
    description: str
    builtin: bool = True


_REGISTRY: dict[str, Analyzer] = {}


def register_analyzer(analyzer: Analyzer) -> None:
    if not analyzer.key:
        raise ValueError("Analyzer.key must be set before registration")
    _REGISTRY[analyzer.key] = analyzer


def get_analyzer(key: str) -> Analyzer | None:
    return _REGISTRY.get(key)


def list_analyzers() -> list[AnalyzerSpec]:
    return sorted(
        (
            AnalyzerSpec(
                key=a.key,
                label=a.label or a.key,
                description=a.description or "",
            )
            for a in _REGISTRY.values()
        ),
        key=lambda s: s.key,
    )


def _reset_for_tests() -> None:
    _REGISTRY.clear()
