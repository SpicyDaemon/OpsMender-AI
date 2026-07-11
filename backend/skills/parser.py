"""Skill definition parser for OpsMender AI.

Reads a SKILL.md (or SKILL.yaml) file and builds an in-memory lookup of
operation classifications.  The file uses YAML front-matter between ``---``
fences for structured data, with optional markdown documentation below.

Expected YAML structure::

    version: "1"
    environment: production
    operations:
      - tool: get_pods
        classification: safe
        tiers:
          T0: {enabled: true, mode: autonomous}
          T1: {enabled: true, mode: autonomous}
          T2: {enabled: true, mode: advisory}
      - tool: cordon_node
        classification: caution
        reversible: true
        compensating_inverse: uncordon_node
        tiers:
          T0: {enabled: true, mode: autonomous, require_reversible: true}
          T1: {enabled: true, mode: approval}
          T2: {enabled: false, mode: blocked}
      - tool: "delete_*"
        classification: destructive
        notes: "Deletes resources — requires Tier 1 approval"
        tiers:
          T0: {enabled: false, mode: blocked}
          T1: {enabled: true, mode: approval}
          T2: {enabled: false, mode: blocked}
"""

from __future__ import annotations

import dataclasses
import fnmatch
import pathlib
import re
from typing import Any, List, Optional

import yaml


_TIER_MODES = ("autonomous", "approval", "blocked", "advisory")
_WORKFLOW_FAILURE_MODES = ("abort", "continue")
_WORKFLOW_TIER_OVERRIDES = (*_TIER_MODES, "T0", "T1", "T2")


@dataclasses.dataclass(frozen=True)
class OperationTierPolicy:
    """Explicit behavior for one operation at one autonomy tier."""

    enabled: bool
    mode: str
    require_reversible: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.mode not in _TIER_MODES:
            raise ValueError(
                f"tier mode must be one of {_TIER_MODES}, got '{self.mode}'"
            )


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
    # ``deny: true`` makes this an explicit deny-list entry — the tier gate
    # blocks it at EVERY tier (deny always wins), regardless of classification.
    deny: bool = False
    # ``allow_generic: true`` opts a generic command-execution tool OUT of the
    # generic-tool guardrail (operator explicitly accepted the risk); normal
    # tier/classification rules then apply. No effect on non-generic tools.
    allow_generic: bool = False
    # ``None`` represents an underspecified programmatic definition. Parsed
    # executable operations always carry complete T0/T1/T2 policies.
    tiers: Optional[dict[int, OperationTierPolicy]] = None

    def __post_init__(self) -> None:
        valid = ("safe", "caution", "destructive")
        # A deny-list entry need not carry a classification — the gate blocks it
        # regardless. Default such entries to "destructive" (the safest label).
        if self.deny and self.classification in ("", "unknown", None):
            object.__setattr__(self, "classification", "destructive")
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

    def policy_for_tier(self, tier: int) -> Optional[OperationTierPolicy]:
        if self.tiers is None:
            return None
        return self.tiers.get(tier)

    @property
    def requires_compensating_inverse(self) -> bool:
        """Return True when Tier 0 needs an explicit inverse for this op."""
        return self.classification != "safe" and self.effective_reversible


@dataclasses.dataclass(frozen=True)
class WorkflowStep:
    """One ordered remediation step declared in a Skill's Workflow section."""

    id: str
    description: str
    tool: str
    inputs: dict[str, Any] = dataclasses.field(default_factory=dict)
    on_failure: str = "abort"
    tier_override: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Workflow step id cannot be blank")
        if not self.tool.strip():
            raise ValueError(f"Workflow step '{self.id}': tool cannot be blank")
        if self.on_failure not in _WORKFLOW_FAILURE_MODES:
            raise ValueError(
                f"Workflow step '{self.id}': on_failure must be one of "
                f"{_WORKFLOW_FAILURE_MODES}"
            )
        if (
            self.tier_override is not None
            and self.tier_override not in _WORKFLOW_TIER_OVERRIDES
        ):
            raise ValueError(
                f"Workflow step '{self.id}': tier_override must be one of "
                f"{_WORKFLOW_TIER_OVERRIDES}"
            )


@dataclasses.dataclass
class SkillDefinition:
    """Parsed skill definition with a fast lookup method."""

    version: str
    environment: str
    operations: List[OperationClassification]
    # Optional session-tier default used after explicit request and
    # service-specific policy, but before the organization default.
    default_tier: Optional[int] = None
    # Optional free-form list of areas the operator wants Environment Scans
    # to weight (e.g. "crashlooping containers", "tasks stuck in PROVISIONING",
    # "high systemd restart counts"). Platform-agnostic by design — the LLM
    # decides what each phrase means given the MCP server's tools.
    focus_areas: List[str] = dataclasses.field(default_factory=list)
    # Optional ordered remediation workflow parsed from ``## Workflow``.
    workflow: List[WorkflowStep] = dataclasses.field(default_factory=list)
    # Operator-authored Markdown guidance from each tier's
    # ``### Custom Instructions`` section. This guides model reasoning only;
    # structured operation policy remains the execution authority.
    custom_instructions: dict[int, str] = dataclasses.field(default_factory=dict)

    def instructions_for_tier(self, tier: int) -> str:
        """Return the free-form guidance for the active autonomy tier."""
        return self.custom_instructions.get(tier, "")

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

    def is_denied(self, tool_name: str) -> bool:
        """Return True when *tool_name* matches an explicit deny-list entry."""
        op = self._match(tool_name)
        return bool(op is not None and op.deny)

    def allows_generic(self, tool_name: str) -> bool:
        """Return True when an exact policy entry opts this generic tool out of
        the generic-tool guardrail (``allow_generic: true``)."""
        op = self._match(tool_name)
        return bool(op is not None and op.allow_generic and not op.deny)

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

    def tier_policy(self, tool_name: str, tier: int) -> Optional[OperationTierPolicy]:
        """Return explicit policy for a declared operation/tier, if present."""
        op = self._match(tool_name)
        return op.policy_for_tier(tier) if op is not None else None

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
        policy = op.policy_for_tier(0)
        require_reversible = (
            True
            if op.tiers is None or policy is None
            else policy.require_reversible is not False
        )
        if not require_reversible:
            return None
        if not op.effective_reversible:
            return "not marked reversible in skill definition"
        if op.requires_compensating_inverse and not op.compensating_inverse:
            return "side-effecting Tier 0 operations must declare compensating_inverse"
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


def _parse_tier(raw: object, *, field: str) -> int:
    """Parse ``0``/``T0``/``Tier 0`` forms without widening the tier range."""
    if isinstance(raw, bool):
        raise ValueError(f"{field} must be T0, T1, or T2")
    if isinstance(raw, int):
        tier = raw
    else:
        value = str(raw).strip().upper().replace("TIER", "").strip()
        if value.startswith("T"):
            value = value[1:]
        try:
            tier = int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be T0, T1, or T2") from exc
    if tier not in (0, 1, 2):
        raise ValueError(f"{field} must be T0, T1, or T2")
    return tier


def _parse_bool(raw: object, *, field: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{field} must be true or false")
    return raw


def _validate_tier_policy(*, tool: str, tier: int, policy: OperationTierPolicy) -> None:
    if tier == 2 and policy.mode not in {"advisory", "blocked"}:
        raise ValueError(
            f"Operation '{tool}' T2 mode must be advisory or blocked, "
            f"got '{policy.mode}'"
        )
    if not policy.enabled and policy.mode not in {"blocked", "advisory"}:
        raise ValueError(
            f"Operation '{tool}' T{tier}: enabled false requires blocked or "
            "advisory mode"
        )
    if policy.enabled and policy.mode == "blocked":
        raise ValueError(
            f"Operation '{tool}' T{tier}: enabled true cannot use blocked mode"
        )


def _extract_workflow_section(text: str) -> object | None:
    """Parse YAML from a markdown ``## Workflow`` section.

    The section may contain either a fenced YAML block or plain YAML. Parsing
    stops at the next level-two heading so later Skill documentation remains
    free-form markdown.
    """
    match = re.search(r"(?im)^##[ \t]+Workflow[ \t]*$", text)
    if match is None:
        return None
    remainder = text[match.end() :]
    next_heading = re.search(r"(?m)^##[ \t]+", remainder)
    section = remainder[: next_heading.start()] if next_heading else remainder
    section = section.strip()
    fenced = re.fullmatch(
        r"```(?:yaml|yml)?[ \t]*\r?\n(?P<body>.*?)\r?\n```",
        section,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced is not None:
        section = fenced.group("body")
    if not section.strip():
        return []
    return yaml.safe_load(section)


def _extract_custom_instructions(text: str) -> dict[int, str]:
    """Extract per-tier free-form Markdown from a SKILL.md document.

    Instructions belong to the nearest ``## Tier N`` section and begin below
    its ``### Custom Instructions`` heading. Content continues to the next
    level-two section, so nested Markdown headings remain available to the
    model. Markdown is preserved apart from surrounding whitespace.
    """
    tier_headings = list(
        re.finditer(r"(?im)^##[ \t]+Tier[ \t]*([012])\b[^\r\n]*$", text)
    )
    instructions: dict[int, str] = {}
    for tier_heading in tier_headings:
        tier = int(tier_heading.group(1))
        next_section = re.search(r"(?m)^##[ \t]+", text[tier_heading.end() :])
        section_end = (
            tier_heading.end() + next_section.start() if next_section else len(text)
        )
        section = text[tier_heading.end() : section_end]
        custom_heading = re.search(
            r"(?im)^###[ \t]+Custom[ \t]+Instructions[ \t]*$", section
        )
        if custom_heading is None:
            continue
        content = section[custom_heading.end() :]
        # Generated templates separate tier sections with a horizontal rule.
        content = re.sub(r"(?:\r?\n)*[ \t]*---[ \t]*$", "", content.rstrip())
        content = content.strip()
        if content:
            if tier in instructions:
                raise ValueError(f"Tier {tier} has duplicate Custom Instructions")
            instructions[tier] = content
    return instructions


def _parse_yaml_custom_instructions(raw: object) -> dict[int, str]:
    """Parse the equivalent per-tier mapping for raw YAML skill files."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("custom_instructions must be a tier-to-text mapping")
    instructions: dict[int, str] = {}
    for raw_tier, value in raw.items():
        tier = _parse_tier(raw_tier, field="custom_instructions tier")
        content = str(value).strip()
        if content:
            instructions[tier] = content
    return instructions


def _normalize_tier_override(raw: object, *, step_id: str) -> Optional[str]:
    if raw is None:
        return None
    value = str(raw).strip()
    lowered = value.lower()
    if lowered in _TIER_MODES:
        return lowered
    tier = _parse_tier(raw, field=f"Workflow step '{step_id}' tier_override")
    return f"T{tier}"


def _parse_workflow(raw_workflow: object | None) -> list[WorkflowStep]:
    if raw_workflow is None:
        return []
    if isinstance(raw_workflow, dict):
        raw_steps = raw_workflow.get("steps", [])
    else:
        raw_steps = raw_workflow
    if not isinstance(raw_steps, list):
        raise ValueError("Workflow must be a list of steps or a mapping with steps")

    workflow: list[WorkflowStep] = []
    seen_ids: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise ValueError(f"Workflow step {index + 1} must be a mapping")
        step_id = str(raw_step.get("id", "")).strip()
        if step_id in seen_ids:
            raise ValueError(f"Workflow step id '{step_id}' is duplicated")
        seen_ids.add(step_id)
        inputs = raw_step.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise ValueError(f"Workflow step '{step_id}': inputs must be a mapping")
        workflow.append(
            WorkflowStep(
                id=step_id,
                description=str(raw_step.get("description", "")).strip(),
                tool=str(raw_step.get("tool", "")).strip(),
                inputs=dict(inputs),
                on_failure=str(raw_step.get("on_failure", "abort")).strip().lower(),
                tier_override=_normalize_tier_override(
                    raw_step.get("tier_override"),
                    step_id=step_id,
                ),
            )
        )
    return workflow


def loads(raw: str, *, fmt: str = "md") -> SkillDefinition:
    """Parse a raw skill definition string and return a SkillDefinition.

    ``fmt`` accepts ``"md"`` (markdown with YAML front-matter, the default)
    or ``"yaml"`` (raw YAML).
    """
    if fmt in ("yaml", "yml"):
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            raise ValueError("Skill definition YAML must be a mapping")
        raw_workflow = data.get("workflow")
        custom_instructions = _parse_yaml_custom_instructions(
            data.get("custom_instructions")
        )
    else:
        front_matter = _extract_yaml_front_matter(raw)
        data = yaml.safe_load(front_matter) or {}
        if not isinstance(data, dict):
            raise ValueError("Skill definition YAML must be a mapping")
        raw_workflow = _extract_workflow_section(raw)
        if raw_workflow is None:
            raw_workflow = data.get("workflow")
        custom_instructions = _extract_custom_instructions(raw)

    raw_operations = data.get("operations", [])
    if not isinstance(raw_operations, list):
        raise ValueError("Skill operations must be a list")

    operations: list[OperationClassification] = []
    for index, entry in enumerate(raw_operations):
        if not isinstance(entry, dict):
            raise ValueError(f"Operation {index + 1} must be a mapping")
        tool = str(entry.get("tool", "")).strip()
        deny = bool(entry.get("deny", False))
        reversible_raw = entry.get("reversible")
        reversible: Optional[bool]
        if reversible_raw is None:
            reversible = None
        else:
            reversible = bool(reversible_raw)
        tiers: Optional[dict[int, OperationTierPolicy]] = None
        if not deny:
            if "tiers" not in entry:
                raise ValueError(
                    f"Operation '{tool}': tiers are required for non-deny operations"
                )
            tiers = {}
            raw_tiers = entry.get("tiers") or {}
            if not isinstance(raw_tiers, dict):
                raise ValueError(f"Operation '{tool}': tiers must be a mapping")
            for raw_tier, raw_policy in raw_tiers.items():
                tier = _parse_tier(raw_tier, field=f"Operation '{tool}' tier")
                if tier in tiers:
                    raise ValueError(f"Operation '{tool}': duplicate T{tier} policy")
                if not isinstance(raw_policy, dict):
                    raise ValueError(
                        f"Operation '{tool}': T{tier} policy must be a mapping"
                    )
                policy = OperationTierPolicy(
                    enabled=_parse_bool(
                        raw_policy.get("enabled", False),
                        field=f"Operation '{tool}' T{tier} enabled",
                    ),
                    mode=str(raw_policy.get("mode", "blocked")).strip().lower(),
                    require_reversible=(
                        None
                        if "require_reversible" not in raw_policy
                        else _parse_bool(
                            raw_policy.get("require_reversible"),
                            field=(f"Operation '{tool}' T{tier} require_reversible"),
                        )
                    ),
                )
                _validate_tier_policy(tool=tool, tier=tier, policy=policy)
                tiers[tier] = policy
            missing = sorted({0, 1, 2} - set(tiers))
            if missing:
                labels = ", ".join(f"T{tier}" for tier in missing)
                raise ValueError(f"Operation '{tool}': missing tier policies: {labels}")
        operations.append(
            OperationClassification(
                tool=tool,
                classification=entry.get("classification", "unknown"),
                notes=entry.get("notes"),
                reversible=reversible,
                compensating_inverse=entry.get("compensating_inverse"),
                deny=deny,
                allow_generic=bool(entry.get("allow_generic", False)),
                tiers=tiers,
            )
        )

    raw_focus = data.get("focus_areas") or []
    focus_areas: list[str] = []
    if isinstance(raw_focus, list):
        focus_areas = [str(item).strip() for item in raw_focus if str(item).strip()]
    elif isinstance(raw_focus, str):
        # Allow a single comma-separated string for convenience.
        focus_areas = [s.strip() for s in raw_focus.split(",") if s.strip()]

    return SkillDefinition(
        version=str(data.get("version", "1")),
        environment=data.get("environment", "default"),
        operations=operations,
        default_tier=(
            None
            if data.get("default_tier") is None
            else _parse_tier(data.get("default_tier"), field="default_tier")
        ),
        focus_areas=focus_areas,
        workflow=_parse_workflow(raw_workflow),
        custom_instructions=custom_instructions,
    )


def load(path: pathlib.Path | str) -> SkillDefinition:
    """Parse a SKILL.md or SKILL.yaml file and return a SkillDefinition."""
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Skill definition not found: {p}")

    raw = p.read_text(encoding="utf-8")
    fmt = "yaml" if p.suffix in (".yaml", ".yml") else "md"
    return loads(raw, fmt=fmt)
