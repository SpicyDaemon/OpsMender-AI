"""Deterministic conversion of legacy skill operations to explicit tiers."""

from __future__ import annotations

import copy
import dataclasses
from typing import Any

import yaml

CONVERSION_NOTICE = (
    "Converted classification-only operations to explicit T0/T1/T2 policies "
    "using conservative compatibility defaults. Review the generated tier "
    "policy before use."
)

_COMPATIBILITY_TIERS: dict[str, dict[str, Any]] = {
    "T0": {
        "enabled": True,
        "mode": "autonomous",
        "require_reversible": True,
    },
    "T1": {"enabled": True, "mode": "approval"},
    "T2": {"enabled": False, "mode": "advisory"},
}


@dataclasses.dataclass(frozen=True)
class SkillConversion:
    content: str
    converted_operations: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.converted_operations)

    @property
    def notice(self) -> str | None:
        return CONVERSION_NOTICE if self.changed else None


def _markdown_parts(raw: str) -> tuple[str, str, str, str] | None:
    lines = raw.splitlines(keepends=True)
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) < 2:
        return None
    start, end = fences[0], fences[1]
    return (
        "".join(lines[: start + 1]),
        "".join(lines[start + 1 : end]),
        lines[end],
        "".join(lines[end + 1 :]),
    )


def convert_legacy_skill_content(raw: str, *, fmt: str = "md") -> SkillConversion:
    """Add compatibility tier policies to classification-only operations.

    Existing explicit policies are never modified. Deny entries need no tiers.
    Markdown outside YAML front matter is preserved byte-for-byte.
    """
    parts = _markdown_parts(raw) if fmt not in {"yaml", "yml"} else None
    yaml_text = parts[1] if parts is not None else raw
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict):
        raise ValueError("Skill definition YAML must be a mapping")

    operations = data.get("operations", [])
    if not isinstance(operations, list):
        raise ValueError("Skill operations must be a list")

    converted: list[str] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"Operation {index + 1} must be a mapping")
        if bool(operation.get("deny", False)) or "tiers" in operation:
            continue
        tool = str(operation.get("tool", "")).strip()
        operation["tiers"] = copy.deepcopy(_COMPATIBILITY_TIERS)
        converted.append(tool or f"operation-{index + 1}")

    if not converted:
        return SkillConversion(content=raw)

    dumped = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    if parts is None:
        content = dumped
    else:
        opening, _, closing, suffix = parts
        content = f"{opening}{dumped}{closing}{suffix}"
    return SkillConversion(content=content, converted_operations=tuple(converted))
