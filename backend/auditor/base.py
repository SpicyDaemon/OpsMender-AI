"""Analyzer base class and shared dataclasses for the Auditor (Sprint 32)."""

from __future__ import annotations

import abc
import dataclasses
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.mcp.pool import MCPServerPool


VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclasses.dataclass(slots=True)
class FindingDraft:
    """In-memory finding produced by an analyzer before persistence."""

    analyzer: str
    severity: str
    message: str
    category: str | None = None
    resource: str | None = None
    suggested_fix: str | None = None

    def normalized_severity(self) -> str:
        sev = (self.severity or "info").lower().strip()
        return sev if sev in VALID_SEVERITIES else "info"


@dataclasses.dataclass(slots=True)
class AnalyzerContext:
    """Per-run context handed to every analyzer.

    Analyzers receive a live DB session, the org_id of the run, the global
    MCP pool, the loaded AppConfig, and per-analyzer parameters drawn from
    the audit request.
    """

    db: AsyncSession
    org_id: uuid.UUID
    pool: MCPServerPool
    config: AppConfig
    params: dict[str, Any] = dataclasses.field(default_factory=dict)


class AnalyzerError(RuntimeError):
    """Raised by an analyzer when its run cannot produce findings."""


class Analyzer(abc.ABC):
    """Base class for all auditor analyzers.

    Subclasses must declare ``key`` (stable identifier persisted on findings),
    ``label`` (human-readable display name), and ``description`` (one-liner).
    The ``run`` coroutine returns a list of :class:`FindingDraft`. Analyzers
    are stateless — one instance is reused across runs.
    """

    key: str = ""
    label: str = ""
    description: str = ""

    @abc.abstractmethod
    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        """Execute the analyzer and return raw findings."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<Analyzer {self.key!r}>"
