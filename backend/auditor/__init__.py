"""Auditor (Sprint 32) — read-only environment scans producing findings reports.

The Auditor module replaces the legacy Detector flow's "treat findings as
incidents" pattern. Audits run on demand, fan out across one or more
analyzers, and persist a separate ``audit_findings`` data model.
"""

from backend.auditor.base import (
    Analyzer,
    AnalyzerContext,
    AnalyzerError,
    FindingDraft,
)
from backend.auditor.registry import (
    AnalyzerSpec,
    get_analyzer,
    list_analyzers,
    register_analyzer,
)
from backend.auditor.runner import run_audit

# Default analyzer registered on import. Per D-001 (MCP-first) and D-023
# (deployment-platform agnosticism), the only built-in analyzer is the
# platform-agnostic LLM-driven Environment Scan. Operator-authored
# adapters live in :mod:`backend.auditor.example_analyzers` and can be
# registered explicitly at startup if deterministic per-platform output
# is required.
from backend.auditor import analyzers as _analyzers  # noqa: F401

register_analyzer(_analyzers.EnvironmentScanAnalyzer())

__all__ = [
    "Analyzer",
    "AnalyzerContext",
    "AnalyzerError",
    "AnalyzerSpec",
    "FindingDraft",
    "get_analyzer",
    "list_analyzers",
    "register_analyzer",
    "run_audit",
]
