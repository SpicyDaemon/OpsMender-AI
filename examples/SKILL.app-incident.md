---
version: "1"
environment: app-incident-response
operations:
  # ─────────────────────────────────────────────────────────────
  # Safe (read-only) — diagnosis surface
  # ─────────────────────────────────────────────────────────────

  # GitHub / source-control MCP — repo + code lookups
  - tool: get_repository
    classification: safe
  - tool: get_file_contents
    classification: safe
    notes: "Read source files for root-cause analysis"
  - tool: list_commits
    classification: safe
  - tool: get_commit
    classification: safe
  - tool: list_pull_requests
    classification: safe
  - tool: get_pull_request
    classification: safe
  - tool: list_issues
    classification: safe
  - tool: get_issue
    classification: safe
  - tool: search_code
    classification: safe
    notes: "Grep across the repo to locate the offending symbol"

  # Jira MCP — ticket lookups
  - tool: jira_search
    classification: safe
  - tool: jira_get_issue
    classification: safe
  - tool: jira_list_projects
    classification: safe
  - tool: jira_get_transitions
    classification: safe

  # Observability MCP — app telemetry for diagnosis
  - tool: query_logs
    classification: safe
    notes: "Loki / CloudWatch Logs / Datadog Logs query"
  - tool: query_metrics
    classification: safe
    notes: "Prometheus / Datadog metrics query"
  - tool: get_trace
    classification: safe
    notes: "Distributed trace lookup (Tempo / Jaeger / Datadog APM)"
  - tool: list_error_events
    classification: safe
    notes: "Sentry / Rollbar error aggregation"

  # ─────────────────────────────────────────────────────────────
  # Caution (non-destructive writes) — proposes fixes for humans
  # ─────────────────────────────────────────────────────────────
  # Code changes never land on a protected branch directly — they
  # always go through a PR / MR so a human reviews the diff before
  # merge. Ticket writes are additive (create / comment / transition).

  - tool: create_branch
    classification: caution
    reversible: true
    compensating_inverse: delete_branch
    notes: "Cuts a fix branch off the default branch — reversible by deleting the branch"
  - tool: create_or_update_file
    classification: caution
    notes: "Writes the proposed patch onto the fix branch — only ever the fix branch, never main/master"
  - tool: push_files
    classification: caution
    notes: "Batch variant of create_or_update_file — same constraint: fix branch only"
  - tool: create_pull_request
    classification: caution
    notes: "Opens a PR with the diagnosis in the description and a link back to the OpsMender incident"
  - tool: create_merge_request
    classification: caution
    notes: "GitLab equivalent of create_pull_request"
  - tool: add_pr_comment
    classification: caution
  - tool: request_reviewers
    classification: caution

  - tool: jira_create_issue
    classification: caution
    notes: "Files a bug ticket with the diagnosis, linked logs/traces, and the PR URL once opened"
  - tool: jira_add_comment
    classification: caution
  - tool: jira_update_issue
    classification: caution
    notes: "Updates fields like priority, assignee, labels, fix-version"
  - tool: jira_transition_issue
    classification: caution
    notes: "Moves the ticket through workflow states (To Do → In Progress → In Review)"
  - tool: jira_link_issue
    classification: caution
    notes: "Links the new ticket to the originating incident / parent epic"

  # ─────────────────────────────────────────────────────────────
  # Destructive — require explicit human approval at every tier
  # ─────────────────────────────────────────────────────────────
  # The agent must NEVER merge its own PR, push to a protected
  # branch, or delete tickets. These exist so the classification is
  # explicit; the tier gate keeps them gated on approval.

  - tool: merge_pull_request
    classification: destructive
    notes: "Agent must not merge its own PR — humans approve and merge"
  - tool: merge_merge_request
    classification: destructive
    notes: "GitLab equivalent — humans only"
  - tool: delete_file
    classification: destructive
  - tool: delete_branch
    classification: destructive
    notes: "Allowed only as a rollback of an agent-created fix branch"
  - tool: force_push
    classification: destructive
  - tool: jira_delete_issue
    classification: destructive
---

# Application Incident Response — Skill Definition

This skill profiles an **application-layer** incident response surface,
distinct from the infra-layer Kubernetes skill in
[`SKILL.md`](SKILL.md). It is aimed at teams who want OpsMender to help
triage app bugs, file tickets, and **propose code fixes as pull
requests / merge requests** — never as direct commits to a protected
branch.

## What this skill enables

Wire the matching MCP servers (GitHub or GitLab; Jira; your
logs/metrics/traces/errors provider) and the agent can:

1. **Diagnose** — pull recent logs, metrics, traces, and error events
   for the failing service; locate the offending code via repo search
   and file reads.
2. **File a Jira ticket** — `jira_create_issue` with the diagnosis,
   linked telemetry, severity, and a link back to the OpsMender
   incident. Transition + comment as the investigation progresses.
3. **Propose a code fix as a PR / MR** — `create_branch` →
   `create_or_update_file` (or `push_files`) → `create_pull_request`
   (or `create_merge_request`). The PR body carries the diagnosis,
   the reasoning, the test plan, and a back-link to the OpsMender
   incident + Jira ticket.
4. **Hand off to humans** — request reviewers, comment on the PR, and
   transition the Jira ticket to *In Review*. The agent stops there.

## Hard rules baked into the classifications

- **The agent never merges its own PR.** `merge_pull_request` /
  `merge_merge_request` are `destructive` and gated on explicit human
  approval (Tier 1) or blocked entirely (Tier 2). A human reviews and
  merges.
- **No writes to protected branches.** `create_or_update_file` and
  `push_files` are only ever issued against an agent-created fix
  branch. Branch protection on `main` / `master` should be configured
  on the SCM side as a belt-and-suspenders enforcement.
- **No ticket deletion.** `jira_delete_issue` is `destructive`. Tickets
  the agent files stay in history; close them through a transition.

## How the tiers shape behavior

OpsMender uses a **3-tier AI Autonomy model** (Tier 2 is the default; Tier 3 is
removed and any legacy stored `3` is remapped to Tier 2).

| Tier | Diagnosis (safe) | File Jira ticket (caution) | Open PR/MR with fix (caution) | Merge PR/MR (destructive) |
|------|------------------|----------------------------|-------------------------------|---------------------------|
| **0 — Autonomous** (sandbox only) | autonomous | autonomous | autonomous | still blocked — destructive |
| **1 — Approval Required** | autonomous | after approval | after approval | after approval |
| **2 — Advisory Only** *(default)* | autonomous (read-only) | recommendation only | recommendation only | recommendation only |

At the default **Tier 2 (Advisory Only)** the agent diagnoses and *recommends* a
ticket + PR but does not write — a human performs the writes. Move to **Tier 1**
to let the agent file the ticket and open the draft PR after operator approval,
while a human still owns the merge.

## Customizing

Copy this file, drop tools you don't expose, and rename the providers
to match your MCP server's tool surface (e.g. Bitbucket → `create_pull_request`,
Linear → `linear_create_issue` instead of Jira, GitHub → `create_pull_request`,
GitLab → `create_merge_request`). Wildcards (`*`, `?`) are supported in
tool names if your MCP server uses a consistent prefix.

Pair this skill with the Kubernetes skill in [`SKILL.md`](SKILL.md) when
the same agent needs to handle both "the pod is crashlooping" (infra)
and "the pod is crashlooping because of a null-pointer in `OrderService`"
(app — fix it in code).
