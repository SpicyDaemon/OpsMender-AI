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

DEFAULT_TEMPLATE_NAME = "New MCP Skill (from template)"


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
    desc_line = description.strip() or "Describe what this skill governs and the environment it applies to."
    return f"""---
version: "1"
environment: {environment}
operations:
  # Use EXACT MCP tool/action identifiers here. Classification drives the
  # backend tier gate: safe -> read/low-risk, caution -> reversible writes,
  # destructive -> high-risk/irreversible. Unknown tools are denied.
  - tool: get_*
    classification: safe
    notes: "Read-only observation — runs at Tier 0/Tier 1."
  - tool: restart_service
    classification: caution
    reversible: true
    notes: "Example reversible remediation — Tier 0 (if allowed) / Tier 1."
  - tool: delete_*
    classification: destructive
    notes: "Example destructive op — Tier 0 only within policy; Tier 1 approval."
focus_areas: []
---

# {name}

> **Skills guide the AI. The backend tier gate enforces what can actually run.**
>
> AI Autonomy Tier controls how much the agent may do during a session. It is
> **separate** from incident priority (P0–P3) and user role (Admin/Operator/Viewer).

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
