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
    """A single tool-name → classification mapping."""

    tool: str  # exact name or glob pattern (e.g. "delete_*")
    classification: str  # "safe" | "caution" | "destructive"
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        valid = ("safe", "caution", "destructive")
        if self.classification not in valid:
            raise ValueError(
                f"Operation '{self.tool}': classification must be one of {valid}, "
                f"got '{self.classification}'"
            )


@dataclasses.dataclass
class SkillDefinition:
    """Parsed skill definition with a fast lookup method."""

    version: str
    environment: str
    operations: List[OperationClassification]

    def classify(self, tool_name: str) -> str:
        """Return the classification for *tool_name*.

        Checks exact matches first, then glob patterns.
        Returns ``"unknown"`` if no rule matches.
        """
        # Exact match pass
        for op in self.operations:
            if op.tool == tool_name:
                return op.classification
        # Glob/wildcard pass
        for op in self.operations:
            if "*" in op.tool or "?" in op.tool:
                if fnmatch.fnmatch(tool_name, op.tool):
                    return op.classification
        return "unknown"


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
        operations.append(
            OperationClassification(
                tool=entry.get("tool", ""),
                classification=entry.get("classification", "unknown"),
                notes=entry.get("notes"),
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
