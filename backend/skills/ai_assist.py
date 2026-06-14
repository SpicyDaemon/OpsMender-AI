"""AI-assisted MCP Skill drafting (MCP Skill Studio).

This layers the configured LLM on top of the deterministic heuristic suggester:
given a freeform operator *intent* and a server's discovered tools, the model
proposes a classification + rationale per tool and authors per-tier custom
instructions. It is an **assist**, not an authority:

  - The operator reviews and can override every row.
  - Arbitrary-command tools (``shell``, ``kubectl``, ``run_command`` …) are
    **forced to deny** regardless of what the model returns — the model can
    never relax the generic-command guardrail.
  - When the model's classification is *less restrictive* than OpsMender's own
    heuristic, the row is flagged ``needs_review`` so the operator notices the
    downgrade.
  - The generated draft still goes through ``build_skill_from_tools`` and the
    same parser the tier gate uses; the tier gate remains the execution
    authority.

The prompt-building and response-parsing here are pure and deterministic so they
can be unit-tested without a live model. The route owns the I/O (model
resolution + completion call).
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from backend.skills.suggest import suggest_classification

_VALID = ("safe", "caution", "destructive")
# Restrictiveness ordering for the conservative-merge check.
_RANK = {"safe": 0, "caution": 1, "destructive": 2}
_MAX_RATIONALE = 300
_MAX_INSTRUCTION = 4000


@dataclasses.dataclass(frozen=True)
class AISuggestedTool:
    name: str
    classification: str
    deny: bool
    allow_generic: bool
    reversible: bool | None
    generic: bool
    needs_review: bool
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class AISuggestResult:
    tools: list[AISuggestedTool]
    tier0_instructions: str
    tier1_instructions: str
    tier2_instructions: str
    environment: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tools": [t.as_dict() for t in self.tools],
            "tier0_instructions": self.tier0_instructions,
            "tier1_instructions": self.tier1_instructions,
            "tier2_instructions": self.tier2_instructions,
            "environment": self.environment,
        }


_PROMPT = """\
You help an operator classify Model Context Protocol (MCP) tools for an incident-\
response AI's safety policy. The AI runs at one of three autonomy tiers: Tier 0 \
(autonomous), Tier 1 (approval-required), Tier 2 (advisory-only).

Classify each tool by RISK:
- "safe": read-only / observation / no state change (get, list, describe, logs).
- "caution": reversible writes (restart, scale, update, rollback, cordon).
- "destructive": high-risk or irreversible (delete, drop, terminate, data loss).

Set "deny": true for tools that must NEVER run automatically — irreversible data \
loss, or arbitrary-command runners (shell, bash, kubectl, run_command, sql, …) \
whose name does not bound what they can do.
Set "reversible": true only for a "caution" tool that can be safely undone.
Keep each "rationale" to one short sentence.

Be conservative: when unsure, choose the MORE restrictive classification. Never \
mark an arbitrary-command tool "safe".

Operator intent / environment context:
{intent}

Tools to classify:
{tool_list}

Return ONLY a JSON object (no prose, no code fences) of this exact shape:
{{
  "environment": "short label, e.g. production",
  "tools": [
    {{"name": "<tool name>", "classification": "safe|caution|destructive", "deny": false, "reversible": false, "rationale": "one sentence"}}
  ],
  "tier0_instructions": "guidance for autonomous remediation",
  "tier1_instructions": "guidance for approval-gated response",
  "tier2_instructions": "advisory-only guidance"
}}
"""


def build_prompt(
    *,
    intent: str,
    environment: str,
    tools: list[dict[str, Any]],
) -> str:
    """Build the strict-JSON classification prompt (pure)."""
    intent_block = (intent or "").strip() or "(no additional context provided)"
    if environment.strip():
        intent_block = f"Environment: {environment.strip()}\n{intent_block}"

    lines = []
    for t in tools:
        name = str(t.get("name", "")).strip()
        if not name:
            continue
        desc = str(t.get("description") or "").strip().replace("\n", " ")
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    tool_list = "\n".join(lines) or "(none)"
    return _PROMPT.format(intent=intent_block, tool_list=tool_list)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Robustly extract a JSON object from model output."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start = text.find("{") if isinstance(text, str) else -1
        end = text.rfind("}") if isinstance(text, str) else -1
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def parse_ai_response(
    text: str,
    *,
    tools: list[dict[str, Any]],
) -> AISuggestResult:
    """Parse + sanitize the model's response into a conservative result (pure).

    Every tool the operator sent appears in the result. Missing/garbled model
    entries fall back to the heuristic suggester. Generic command tools are
    force-denied, and any model classification less restrictive than the
    heuristic is flagged ``needs_review``.
    """
    data = _parse_json(text) or {}
    raw_tools = data.get("tools")
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(raw_tools, list):
        for entry in raw_tools:
            if isinstance(entry, dict):
                key = str(entry.get("name", "")).strip().lower()
                if key:
                    by_name[key] = entry

    out: list[AISuggestedTool] = []
    for tool in tools:
        name = str(tool.get("name", "")).strip()
        if not name:
            continue
        heuristic = suggest_classification(name)
        ai = by_name.get(name.lower())

        if ai is None:
            # Model omitted this tool — keep the deterministic suggestion.
            out.append(
                AISuggestedTool(
                    name=name,
                    classification=heuristic.classification,
                    deny=heuristic.deny,
                    allow_generic=False,
                    reversible=None,
                    generic=heuristic.generic,
                    needs_review=True,
                    rationale=heuristic.rationale,
                )
            )
            continue

        classification = str(ai.get("classification", "")).strip().lower()
        if classification not in _VALID:
            classification = heuristic.classification
        deny = _coerce_bool(ai.get("deny", False))
        reversible = ai.get("reversible")
        reversible_b = _coerce_bool(reversible) if reversible is not None else None
        rationale = str(ai.get("rationale", "")).strip()[:_MAX_RATIONALE] or heuristic.rationale
        needs_review = False

        # Hard guardrail: a generic command tool is always denied + destructive,
        # no matter what the model proposed.
        if heuristic.generic:
            deny = True
            classification = "destructive"
            needs_review = True

        # Flag a model downgrade relative to the deterministic heuristic so the
        # operator notices a less-restrictive suggestion.
        if not deny and _RANK[classification] < _RANK[heuristic.classification]:
            needs_review = True

        out.append(
            AISuggestedTool(
                name=name,
                classification=classification,
                deny=deny,
                allow_generic=False,
                reversible=reversible_b,
                generic=heuristic.generic,
                needs_review=needs_review,
                rationale=rationale,
            )
        )

    def _clean(value: Any) -> str:
        return str(value or "").strip()[:_MAX_INSTRUCTION]

    return AISuggestResult(
        tools=out,
        tier0_instructions=_clean(data.get("tier0_instructions")),
        tier1_instructions=_clean(data.get("tier1_instructions")),
        tier2_instructions=_clean(data.get("tier2_instructions")),
        environment=_clean(data.get("environment")) or "your-environment",
    )
