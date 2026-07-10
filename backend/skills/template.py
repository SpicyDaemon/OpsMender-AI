"""MCP Skill Studio — 3-tier skill policy template generator.

Produces a structured Markdown skill policy for the AI Autonomy 3-tier model:

  Tier 0 — Autonomous       (allowed actions, AI action workflow, deny actions)
  Tier 1 — Approval Required (allow list, ask-approval, deny list)
  Tier 2 — Advisory Only     (no actions execute — guidance only)

The Markdown carries YAML front-matter with ``operations`` so the backend tier
gate (``backend/tiers/enforcement.py``) can classify and enforce tools. The
prose sections give the AI human-readable allow/deny/approval guidance and
environment rules. Both the structured front-matter and the prose are saved as
one ``content_md`` blob, which is editable and downloadable.
"""

from __future__ import annotations

from typing import Any, Sequence

import yaml

DEFAULT_TEMPLATE_NAME = "New MCP Skill (from template)"

_VALID_CLASSIFICATIONS = ("safe", "caution", "destructive")


def _normalize_operation(op: dict[str, Any]) -> dict[str, Any]:
    """Coerce one generator operation into a valid front-matter entry.

    Conservative + fail-safe: an unknown/empty classification on a non-deny op
    becomes ``caution`` (never silently ``safe``); deny entries that omit a
    classification are labelled ``destructive`` (matching the parser default).
    """
    tool = str(op.get("tool", "")).strip()
    deny = bool(op.get("deny", False))
    classification = str(op.get("classification", "") or "").strip().lower()
    if classification not in _VALID_CLASSIFICATIONS:
        classification = "destructive" if deny else "caution"

    entry: dict[str, Any] = {"tool": tool, "classification": classification}
    if op.get("reversible") is not None:
        entry["reversible"] = bool(op["reversible"])
    # Tier 0 safety floor: a non-safe op needs a compensating inverse to run
    # autonomously. Emit it so the parser/enforcement can honor it.
    inverse = op.get("compensating_inverse")
    if inverse:
        entry["compensating_inverse"] = str(inverse).strip()
    if deny:
        entry["deny"] = True
    if op.get("allow_generic"):
        entry["allow_generic"] = True
    if not deny:
        if classification == "safe":
            entry["tiers"] = {
                "T0": {"enabled": True, "mode": "autonomous"},
                "T1": {"enabled": True, "mode": "autonomous"},
                "T2": {"enabled": True, "mode": "advisory"},
            }
        else:
            tier0_enabled = bool(
                op.get("reversible") is True and entry.get("compensating_inverse")
            )
            entry["tiers"] = {
                "T0": {
                    "enabled": tier0_enabled,
                    "mode": "autonomous" if tier0_enabled else "blocked",
                    "require_reversible": True,
                },
                "T1": {"enabled": True, "mode": "approval"},
                "T2": {"enabled": False, "mode": "blocked"},
            }
    notes = op.get("notes")
    if notes:
        entry["notes"] = str(notes).strip()
    return entry


def build_skill_from_tools(
    *,
    name: str,
    operations: Sequence[dict[str, Any]],
    environment: str = "your-environment",
    description: str = "",
    tier0_instructions: str = "",
    tier1_instructions: str = "",
    tier2_instructions: str = "",
) -> str:
    """Deterministically build a 3-tier MCP Skill Markdown from classified tools.

    ``operations`` is the operator's reviewed classification of an MCP server's
    discovered tools (each: ``tool``, ``classification``, optional ``deny`` /
    ``allow_generic`` / ``reversible`` / ``notes``). The front-matter
    ``operations`` block is what the backend tier gate enforces; the prose is
    human-readable guidance. No LLM is involved — the output is a pure function
    of the operator's structured input, so it is reproducible and testable.
    """
    norm = [
        _normalize_operation(op) for op in operations if str(op.get("tool", "")).strip()
    ]

    front: dict[str, Any] = {
        "version": "1",
        "environment": (environment or "your-environment").strip()
        or "your-environment",
        "default_tier": "T2",
        "operations": norm,
        "focus_areas": [],
    }
    fm_yaml = yaml.safe_dump(front, sort_keys=False, default_flow_style=False).strip()

    # Categorize for the prose tables.
    denied = [o for o in norm if o.get("deny")]
    safe = [o for o in norm if not o.get("deny") and o["classification"] == "safe"]
    caution = [
        o for o in norm if not o.get("deny") and o["classification"] == "caution"
    ]
    destructive = [
        o for o in norm if not o.get("deny") and o["classification"] == "destructive"
    ]

    def _rows(ops: list[dict[str, Any]]) -> str:
        if not ops:
            return "| _(none)_ | | |\n"
        out = ""
        for o in ops:
            flags = []
            if o.get("allow_generic"):
                flags.append("allow_generic")
            if o.get("reversible") is True:
                flags.append("reversible")
            if o.get("compensating_inverse"):
                flags.append(f"rollback→`{o['compensating_inverse']}`")
            note = o.get("notes", "") or ""
            extra = ", ".join(flags)
            out += f"| `{o['tool']}` | {note} | {extra} |\n"
        return out

    desc_line = (
        description.strip()
        or "Generated in the MCP Skill Studio from discovered MCP tools."
    )
    t0 = tier0_instructions.strip() or "_Freeform guidance for autonomous remediation._"
    t1 = (
        tier1_instructions.strip() or "_Freeform guidance for approval-gated response._"
    )
    t2 = tier2_instructions.strip() or "_Freeform advisory guidance._"

    # Build the deny-list rows outside the return f-string: nesting an f-string
    # (with its `\n`) inside an outer f-string expression is a SyntaxError on
    # Python 3.11. As a standalone statement the `\n` is plain literal text.
    deny_rows = (
        "".join(f"| `{o['tool']}` | {o.get('notes', '') or ''} |\n" for o in denied)
        or "| _(none)_ | |\n"
    )

    return f"""---
{fm_yaml}
---

# {name}

> **Skills guide the AI. The backend tier gate enforces what can actually run.**
>
> Generated in the MCP Skill Studio from discovered MCP tools. The
> classifications below were chosen by an operator (assisted by OpsMender's
> heuristic suggestions); the backend tier gate, deny lists, generic-command
> guardrail, and conservative unknown-deny defaults remain the execution
> authority. Edit anything before saving.

- **Skill name:** {name}
- **Description:** {desc_line}
- **Action classification:** `safe` (read-only / low-risk) · `caution`
  (reversible writes) · `destructive` (high-risk / irreversible). `deny: true`
  blocks an entry at every tier. Generic command tools are auto-guarded.

---

## Tier 0 — Autonomous

The AI may execute remediation automatically — **only within this policy, deny
lists, MCP permissions, and backend guardrails.** Most autonomous, not unlimited.

> **Tier 0 safety floor.** `require_reversible: true` requires
> `reversible: true` and a `compensating_inverse` for non-safe actions. A skill
> may explicitly set `require_reversible: false` for an operation whose T0 mode
> is autonomous. Legacy operations without `tiers` retain the original floor.

### Safe actions (read-only / low-risk)

| MCP tool/action | Notes | Flags |
|---|---|---|
{_rows(safe)}
### Reversible writes (caution)

| MCP tool/action | Notes | Flags |
|---|---|---|
{_rows(caution)}
### Custom Instructions

{t0}

---

## Tier 1 — Approval Required

The AI may investigate and propose actions. Safe actions run; destructive /
high-risk actions pause for operator approval; deny-listed actions never run.

### Destructive / high-risk (approval required)

| MCP tool/action | Notes | Flags |
|---|---|---|
{_rows(destructive)}
### Custom Instructions

{t1}

---

## Tier 2 — Advisory Only

**No actions execute.** The AI provides analysis, recommendations, and runbooks,
and may perform read-only observation. Tier 2 is the **default** for new sessions.

### Custom Instructions

{t2}

---

## Deny list (never executes at any tier)

`deny: true` always wins — over classification, allow lists, and Tier 0.

| MCP tool/action | Notes |
|---|---|
{deny_rows}"""


def build_skill_template(
    *,
    name: str = DEFAULT_TEMPLATE_NAME,
    environment: str = "your-environment",
    description: str = "",
) -> str:
    """Return a fully-formed 3-tier MCP Skill Markdown template.

    The YAML front-matter is intentionally minimal but valid (a couple of
    example operations) so the policy parses and enforces out of the box; the
    operator replaces the examples with their real MCP tool identifiers.
    """
    desc_line = (
        description.strip()
        or "Describe what this skill governs and the environment it applies to."
    )
    return f"""---
version: "1"
environment: {environment}
default_tier: T2
operations:
  # Unknown tools fail closed. Use exact MCP tool identifiers where possible.
  - tool: get_pods
    classification: safe
    tiers:
      T0:
        enabled: true
        mode: autonomous
      T1:
        enabled: true
        mode: autonomous
      T2:
        enabled: true
        mode: advisory
  - tool: restart_deployment
    classification: caution
    reversible: true
    compensating_inverse: restart_deployment_previous_state
    tiers:
      T0:
        enabled: true
        mode: autonomous
        require_reversible: true
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: false
        mode: blocked
  - tool: delete_stuck_pod
    classification: destructive
    reversible: false
    tiers:
      T0:
        enabled: true
        mode: autonomous
        require_reversible: false
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: false
        mode: blocked
  - tool: delete_database
    deny: true
  # Generic tools require allow_generic: true before explicit tier policy is
  # considered. Prefer scoped wrappers over shell/kubectl/run_command.
  - tool: kubectl
    classification: caution
    allow_generic: true
    tiers:
      T0:
        enabled: false
        mode: blocked
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: false
        mode: blocked
focus_areas: []
---

# {name}

> **Skills guide the AI. The backend tier gate enforces what can actually run.**
>
> AI Autonomy Tier controls how much the agent may do during a session. It is
> **separate** from incident priority (P0–P3) and user role (Admin/Operator/Viewer).

> ⚠️ **Generic command tools are high-risk.** Tools such as `shell`, `bash`,
> `run_command`, `kubectl`, `aws_cli`, `gcloud`, `az`, `terraform`, `sql`,
> `python`, or `node` run arbitrary commands, so their name alone does not bound
> what they can do. OpsMender **denies them by default**: blocked at Tier 0 and
> Tier 2, and **approval-required at Tier 1**. Only opt a narrowly-scoped wrapper
> out with `allow_generic: true`, and prefer explicit deny entries
> (`deny: true`) for anything dangerous. Deny always wins.

Action classification (drives the tier gate): `safe` (read-only / low-risk) ·
`caution` (reversible writes) · `destructive` (high-risk / irreversible) ·
`unknown` (unclassified — always denied) · generic-execution (auto-detected
arbitrary-command tools — conservatively guarded). `deny: true` blocks an entry
at every tier.

Each operation may declare `tiers.T0` / `T1` / `T2` with `enabled` and `mode`
(`autonomous`, `approval`, `blocked`, or `advisory`). These structured values
are enforced by `backend/tiers/enforcement.py`. Operations without `tiers`
remain backward compatible and use the legacy classification matrix.

## Metadata

- **Skill name:** {name}
- **Description:** {desc_line}
- **Assignment:** Unassigned · Global fallback · Specific MCP server
  - *Unassigned* = a saved draft. Editable and downloadable, but **never**
    injected into AI sessions and **not** used as the global fallback.
  - *Global fallback* = applies to every MCP server that has no specific skill.
  - *Specific MCP server* = takes precedence over the global fallback.
- **Environment notes:** _e.g. production / staging / dev; blast-radius notes._
- **Owner / maintainer:** _optional_

> **Action identity:** Use exact MCP tool/action identifiers where possible
> (e.g. `k8s_restart_deployment`). Human-readable names help the AI understand
> intent, but exact identifiers make policies easier to enforce.

---

## Tier 0 — Autonomous

The AI may execute remediation actions automatically — including rollbacks,
restarts, failovers, and other destructive operations — **but only within MCP
Skill policy, deny lists, MCP permissions, and backend guardrails.** Tier 0 is
the most autonomous tier; it is **not** unlimited.

### Allowed Actions

Actions the AI may execute autonomously under Tier 0.

| Display name | MCP tool/action identifier | Description | Risk | Environment notes | Prerequisites / checks |
|---|---|---|---|---|---|
| Restart service | `restart_service` | Roll a deployment | medium | reversible | health check passes |
| _Add action_ | `tool_identifier` | _what it does_ | low/med/high | _notes_ | _checks_ |

### AI Action Workflow

Preferred order of operations the AI should follow:

1. Observe current state.
2. Confirm blast radius.
3. Check recent deploys.
4. Try the safest remediation first.
5. Verify the result.
6. Summarize actions taken.

### Deny Actions

Actions the AI must **never** execute (even at Tier 0).

| Display name | MCP tool/action identifier | Reason | Environment notes |
|---|---|---|---|
| Delete database | `delete_database` | irreversible data loss | all environments |
| _Add deny_ | `tool_identifier` | _why_ | _notes_ |

### Custom Instructions

_Freeform guidance for autonomous remediation._

---

## Tier 1 — Approval Required

The AI may investigate and propose actions. Execution of write/remediation
actions must go through the **approval gate** unless explicitly classified as a
safe allowed action by policy. Destructive or high-risk actions require operator
approval before execution.

### Allow List Actions

Safe actions that may be taken under Tier 1 according to policy.

| Display name | MCP tool/action identifier | Description | Approval behavior | Risk | Environment notes |
|---|---|---|---|---|---|
| Get pods | `get_pods` | read-only | auto (no approval) | low | _notes_ |
| _Add action_ | `tool_identifier` | _what it does_ | auto / approval | low/med | _notes_ |

### Ask Approval Actions

Actions the AI may propose but must wait for operator approval before executing.

| Display name | MCP tool/action identifier | Approval prompt guidance | Risk | Environment notes |
|---|---|---|---|---|
| Delete pod | `delete_pod` | "Approve deleting pod X?" | high | confirm blast radius |
| _Add action_ | `tool_identifier` | _prompt_ | med/high | _notes_ |

### Deny List Actions

Actions the AI must never execute under Tier 1.

| Display name | MCP tool/action identifier | Reason | Environment notes |
|---|---|---|---|
| _Add deny_ | `tool_identifier` | _why_ | _notes_ |

### Custom Instructions

_Freeform guidance for approval-gated response._

---

## Tier 2 — Advisory Only

**No actions allowed. Advisory mode only.**

The AI provides analysis, recommendations, and runbooks. It may perform
read-only observation, but **no write/remediation actions execute** — no
restarts, rollbacks, failovers, deletes, or changes to infrastructure, apps, or
config. Tier 2 is the **default** for new sessions.

### Custom Instructions

_Freeform advisory guidance._

Example: Explain the likely root cause, suggest safe commands for a human to
run, and clearly label any action that would modify infrastructure.

---

## Environment Rules (optional)

Use this section when **one** MCP server has access to **multiple** environments
(e.g. `aws-multi-env-mcp`). Define per-environment expectations. These are
examples only — define your own environments.

```text
Production:
- Tier 0: only explicitly allowed rollback-safe actions
- Tier 1: destructive actions require approval
- Tier 2: advisory only

Staging:
- Tier 0: restarts and rollbacks allowed if health checks pass
- Tier 1: approvals required for destructive changes
- Tier 2: advisory only

Development:
- Tier 0: wider autonomous remediation allowed
- Tier 1: approvals for destructive deletes
- Tier 2: advisory only
```

If you instead run **separate MCP servers per environment** (e.g.
`aws-prod-mcp`, `aws-staging-mcp`, `aws-dev-mcp`), create a separate MCP Skill
for each server and tune its tiers per environment.
"""
