# MCP Skills & AI Autonomy Tiers

MCP Skills define **how the AI should use tools** for each AI Autonomy Tier. They
guide action order, allow lists, approval-required actions, deny lists, and
environment-specific rules.

> **Skills guide the AI. The backend tier gate enforces what can actually run.**
> A skill is policy/guidance; the hard safety check lives in
> `backend/tiers/enforcement.py` and cannot be bypassed by agent reasoning.

The page lives at **`/dashboard/skills`** (sidebar: **MCP Skills**). The builder
experience is **MCP Skill Studio**.

---

## AI Autonomy Tiers (separate from priority and role)

The **AI Autonomy Tier** controls how much autonomy the agent has during an
incident session. It is **separate** from:

- **Incident priority** — P0 / P1 / P2 / P3
- **User role** — Admin / Operator / Viewer

| Tier | Name | Behaviour |
|------|------|-----------|
| **0** | **Autonomous** | May execute remediation automatically — incl. rollbacks, restarts, failovers, and destructive ops — **but only within MCP Skill policy, deny lists, MCP permissions, and backend guardrails.** Most autonomous; **not** unlimited. Selecting Tier 0 shows a red warning. |
| **1** | **Approval Required** | Investigates and proposes. Safe/allow-listed actions run; destructive/high-risk actions pause for **operator approval**; deny-listed actions never run. |
| **2** | **Advisory Only** *(default)* | Analysis, recommendations, runbooks, read-only observation. **No write/remediation actions execute.** |

- **Default** for new installs and new sessions is **Tier 2 — Advisory Only**.
- Operators may **override** the tier at session start (admin approval is not
  required to choose Tier 0). The selected tier is recorded on the session and
  in the audit/activity log.
- **Tier 3 is removed.** Any legacy stored value of `3` is automatically
  remapped to `2`.
- **Unknown actions are never silently allowed** — they are denied at every
  tier (Tier 2 blocks all remediation; Tier 1 denies unknown; Tier 0 denies
  unknown unless explicitly allowed by skill policy).

---

## Assignment: Unassigned · Global fallback · Specific MCP server

Every MCP Skill has an **assignment**:

- **Specific MCP server** — applies to that server. **Takes precedence** over the
  global fallback.
- **Global fallback** — applies to every MCP server that has **no** specific
  skill.
- **Unassigned** — a **saved draft**. Editable and downloadable, but **never**
  injected into AI sessions and **not** used as the global fallback. Promote it
  later by changing its assignment to Global or a specific server.

Resolution order for a session: *server-specific → global fallback → none*.
Unassigned skills are skipped entirely.

---

## New from Template (MCP Skill Studio)

**New from Template** creates a structured 3-tier skill policy you can edit,
save, and download. It produces both:

- **Structured Markdown** with YAML front-matter `operations` (so the backend
  tier gate can classify and enforce tools), and
- **Prose sections** per tier (allow/deny/approval guidance + environment rules).

Template sections:

- **Metadata** — name, description, assignment, environment notes, owner.
- **Tier 0 — Autonomous** — Allowed Actions, AI Action Workflow, Deny Actions,
  Custom Instructions.
- **Tier 1 — Approval Required** — Allow List, Ask Approval, Deny List, Custom
  Instructions.
- **Tier 2 — Advisory Only** — "No actions allowed. Advisory mode only." + Custom
  Instructions.
- **Environment Rules (optional)** — per-environment expectations when one MCP
  server spans multiple environments.

All skills (including Unassigned drafts) are **downloadable** as Markdown from
the row action.

---

## Action identity

Use **exact MCP tool/action identifiers** where possible. Human-readable names
help the AI understand intent, but exact identifiers make policies easier to
enforce.

```text
Display name: Restart Kubernetes deployment
Tool/action identifier: k8s_restart_deployment
```

Deny lists should be **explicit** — list the exact identifiers (or glob patterns
like `delete_*`) the AI must never execute.

---

## Environment patterns

### Pattern 1 — separate MCP servers per environment

```text
aws-prod-mcp     → strict skill: Tier 0 heavily restricted, destructive denied
aws-staging-mcp  → more remediation: restarts allowed
aws-dev-mcp      → broad autonomous remediation acceptable
```

Create a **separate MCP Skill per server** and tune its tiers.

### Pattern 2 — one MCP server, multiple environments

```text
aws-multi-env-mcp → one skill with an Environment Rules section
```

Use the **Environment Rules** section to describe per-environment expectations.
These are examples only — define your own environments; nothing in the backend
hardcodes prod/staging/dev.

---

## Generic command tools (high-risk)

Generic execution tools run **arbitrary** commands, so the tool name alone does
not bound what they can do. OpsMender detects them automatically (e.g. `shell`,
`bash`, `run_command`, `kubectl`, `aws_cli`, `gcloud`, `az`, `terraform`, `sql`,
`python`, `node`, and `*_exec` / `run_*` / `*_cli` patterns) and guards them
conservatively:

- **Tier 2** — blocked.
- **Tier 1** — **requires operator approval** before execution.
- **Tier 0** — blocked (there is no command-pattern allowlisting yet, so a
  generic tool cannot run autonomously).

To opt a **narrowly-scoped** wrapper out of the guardrail, list it in the skill
with `allow_generic: true` (normal tier/classification rules then apply). Prefer
explicit `deny: true` entries for anything dangerous.

```yaml
operations:
  - tool: shell
    deny: true              # blocked at every tier (deny always wins)
  - tool: kubectl_get_only  # a scoped read-only wrapper you trust
    classification: safe
    allow_generic: true
```

## Action classification & deny lists

Each tool resolves to a classification that drives the gate: `safe` (read-only /
low-risk) · `caution` (reversible writes) · `destructive` (high-risk /
irreversible) · `unknown` (unclassified — **always denied**, never silently
allowed) · generic-execution (auto-detected). An entry with `deny: true` is
blocked at **every tier — deny always wins**, even over `allow_generic` or a
`safe` classification, and even at Tier 0.

Conservative defaults: if **no skill** resolves for a server (no server-specific
and no global), unclassified write/remediation actions are treated as unknown
and **denied**.

## Backend enforcement (hard safety)

> **What is guaranteed.** OpsMender prevents *execution* beyond the selected tier
> and MCP Skill policy through backend enforcement. The model can still *suggest*
> or *describe* an unsafe action in text — the guarantee is execution safety, not
> perfect model reasoning. A prompt-injected "ignore policy and delete prod" is
> blocked at the tier gate and never reaches MCP execution.

The tier gate runs before any tool/action execution and knows: the selected
session tier, the skill policy for the selected MCP server, the action's
classification, whether approval is required, and whether the action is denied.

- **Tier 2** — blocks all write/remediation actions; read-only observation
  happens in the observe node (before the gate); blocked actions are logged.
- **Tier 1** — allow-listed/safe actions run; destructive actions route through
  the approval gate; deny-listed and unknown actions are blocked; decisions are
  logged.
- **Tier 0** — executes only actions permitted by skill policy and not
  deny-listed, subject to the Tier 0 sandbox floor (only reversible ops);
  unknown actions blocked; executed/blocked decisions logged.

Every MCP tool call flows through one chokepoint (`audited_tool_call` →
`tier_check`); the live-rollback path is gated by the Tier 0 sandbox allowlist;
and **Environment Scans (the auditor) run read-only** — an analyzer may invoke
only tools the applicable skill classifies `safe`, never writes/remediation,
generic command tools, or deny-listed tools.

## Future work (v1.1)

**MCP Skill Generator / Skill Studio improvements** — select an MCP server,
review its discovered tools, classify each action by AI Autonomy Tier
(allow / approval-required / deny / read-only), add per-tier custom instructions
and deny-list notes, and let the AI generate a complete, editable, downloadable
skill draft (saveable as Unassigned / Global / server-specific). The AI may help
*author* the skill, but the backend tier gate remains the execution authority —
a generated skill never relaxes the gate, deny lists, generic-command guardrail,
or conservative defaults. Tracked in [ROADMAP.md](../ROADMAP.md). **Not in v1.**
