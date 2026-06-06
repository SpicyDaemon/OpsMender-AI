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

## Backend enforcement (hard safety)

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
