"""Skill definition parser for AI Incident Manager.

Reads a SKILL.md (or SKILL.yaml) file and builds an in-memory lookup of
operation classifications.  The file uses YAML front-matter between ``---``
fences for structured data, with optional markdown documentation below.

Expected YAML structure::

    version: "1"
    environment: production
    operations:
      - tool: get_pods
        classification: safe
      - tool: cordon_node
        classification: caution
        reversible: true
        compensating_inverse: uncordon_node
      - tool: "delete_*"
        classification: destructive
        notes: "Deletes resources — requires Tier 1 approval"
"""

from __future__ import annotations

import dataclasses
import fnmatch
import pathlib
from typing import List, Optional

import yaml


@dataclasses.dataclass
class OperationClassification:
    """A single tool-name → classification mapping.

    ``reversible`` is the Tier 0 safety floor: a Tier 0 session only executes
    operations that resolve to ``reversible=True``.  When unset it falls back
    to a classification-driven default (``safe`` is implicitly reversible;
    ``caution``/``destructive`` are not).  ``compensating_inverse`` names the
    tool that undoes this one — the rollback engine invokes it with the same
    parameters.
    """

    tool: str  # exact name or glob pattern (e.g. "delete_*")
    classification: str  # "safe" | "caution" | "destructive"
    notes: Optional[str] = None
    reversible: Optional[bool] = None
    compensating_inverse: Optional[str] = None

    def __post_init__(self) -> None:
        valid = ("safe", "caution", "destructive")
        if self.classification not in valid:
            raise ValueError(
                f"Operation '{self.tool}': classification must be one of {valid}, "
                f"got '{self.classification}'"
            )

    @property
    def effective_reversible(self) -> bool:
        """Resolved reversibility, applying the classification default."""
        if self.reversible is not None:
            return self.reversible
        return self.classification == "safe"

    @property
    def requires_compensating_inverse(self) -> bool:
        """Return True when Tier 0 needs an explicit inverse for this op."""
        return self.classification != "safe" and self.effective_reversible


@dataclasses.dataclass
class SkillDefinition:
    """Parsed skill definition with a fast lookup method."""

    version: str
    environment: str
    operations: List[OperationClassification]

    def _match(self, tool_name: str) -> Optional[OperationClassification]:
        """Return the first matching OperationClassification or None."""
        for op in self.operations:
            if op.tool == tool_name:
                return op
        for op in self.operations:
            if "*" in op.tool or "?" in op.tool:
                if fnmatch.fnmatch(tool_name, op.tool):
                    return op
        return None

    def classify(self, tool_name: str) -> str:
        """Return the classification for *tool_name*.

        Checks exact matches first, then glob patterns.
        Returns ``"unknown"`` if no rule matches.
        """
        op = self._match(tool_name)
        return op.classification if op is not None else "unknown"

    def is_reversible(self, tool_name: str) -> bool:
        """Return True if *tool_name* is declared reversible.

        Unknown tools are treated as non-reversible (fail-closed).
        """
        op = self._match(tool_name)
        return op.effective_reversible if op is not None else False

    def inverse_for(self, tool_name: str) -> Optional[str]:
        """Return the compensating-inverse tool name, if any."""
        op = self._match(tool_name)
        return op.compensating_inverse if op is not None else None

    def tier0_violation_reason(self, tool_name: str) -> Optional[str]:
        """Return the Tier 0 safety-floor violation reason, if any.

        Tier 0 runs only operations that are:

        1. declared in the skill definition
        2. reversible (explicitly, or implicitly via ``safe``)
        3. equipped with a ``compensating_inverse`` when they are
           side-effecting writes
        """
        op = self._match(tool_name)
        if op is None:
            return "unknown operation — not declared in skill definition"
        if not op.effective_reversible:
            return "not marked reversible in skill definition"
        if op.requires_compensating_inverse and not op.compensating_inverse:
            return (
                "side-effecting Tier 0 operations must declare "
                "compensating_inverse"
            )
        return None

    def is_tier0_safe(self, tool_name: str) -> bool:
        """Return True if *tool_name* clears the Tier 0 safety floor."""
        return self.tier0_violation_reason(tool_name) is None


def _extract_yaml_front_matter(text: str) -> str:
    """Return the YAML block between the first pair of ``---`` fences."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if start is None:
                start = i + 1
            else:
                return "".join(lines[start:i])
    if start is not None:
        # Single --- at top, treat rest as YAML (no closing fence)
        return "".join(lines[start:])
    # No fences at all — treat entire content as YAML
    return text


def loads(raw: str, *, fmt: str = "md") -> SkillDefinition:
    """Parse a raw skill definition string and return a SkillDefinition.

    ``fmt`` accepts ``"md"`` (markdown with YAML front-matter, the default)
    or ``"yaml"`` (raw YAML).
    """
    if fmt in ("yaml", "yml"):
        data = yaml.safe_load(raw) or {}
    else:
        front_matter = _extract_yaml_front_matter(raw)
        data = yaml.safe_load(front_matter) or {}

    operations: list[OperationClassification] = []
    for entry in data.get("operations", []):
        reversible_raw = entry.get("reversible")
        reversible: Optional[bool]
        if reversible_raw is None:
            reversible = None
        else:
            reversible = bool(reversible_raw)
        operations.append(
            OperationClassification(
                tool=entry.get("tool", ""),
                classification=entry.get("classification", "unknown"),
                notes=entry.get("notes"),
                reversible=reversible,
                compensating_inverse=entry.get("compensating_inverse"),
            )
        )

    return SkillDefinition(
        version=str(data.get("version", "1")),
        environment=data.get("environment", "default"),
        operations=operations,
    )


def load(path: pathlib.Path | str) -> SkillDefinition:
    """Parse a SKILL.md or SKILL.yaml file and return a SkillDefinition."""
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Skill definition not found: {p}")

    raw = p.read_text(encoding="utf-8")
    fmt = "yaml" if p.suffix in (".yaml", ".yml") else "md"
    return loads(raw, fmt=fmt)
