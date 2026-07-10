---
version: "1"
environment: kubernetes-production
tier_policies:
  read: &read_tiers
    T0: {enabled: true, mode: autonomous}
    T1: {enabled: true, mode: autonomous}
    T2: {enabled: true, mode: advisory}
  reversible_write: &reversible_write_tiers
    T0: {enabled: true, mode: autonomous, require_reversible: true}
    T1: {enabled: true, mode: approval}
    T2: {enabled: false, mode: blocked}
  approval_only: &approval_only_tiers
    T0: {enabled: false, mode: blocked}
    T1: {enabled: true, mode: approval}
    T2: {enabled: false, mode: blocked}
operations:
  # --- Safe (read-only) ---
  - tool: get_pods
    classification: safe
    tiers: *read_tiers
  - tool: get_pod_logs
    classification: safe
    tiers: *read_tiers
  - tool: get_namespaces
    classification: safe
    tiers: *read_tiers
  - tool: get_deployments
    classification: safe
    tiers: *read_tiers
  - tool: get_services
    classification: safe
    tiers: *read_tiers
  - tool: get_events
    classification: safe
    tiers: *read_tiers
  - tool: get_nodes
    classification: safe
    tiers: *read_tiers
  - tool: describe_*
    classification: safe
    notes: "All describe operations are read-only"
    tiers: *read_tiers
  - tool: list_*
    classification: safe
    notes: "All list operations are read-only"
    tiers: *read_tiers

  # --- Caution (non-destructive writes) ---
  # `reversible: true` is the Tier 0 safety floor — only reversible ops run
  # autonomously at Tier 0.  `compensating_inverse` names the tool the
  # rollback engine invokes to undo this one (same parameters).
  - tool: scale_deployment
    classification: caution
    reversible: true
    notes: "Changes replica count — reversible but impacts capacity (inverse is parametric, not auto-rolled-back)"
    tiers: *approval_only_tiers
  - tool: cordon_node
    classification: caution
    reversible: true
    compensating_inverse: uncordon_node
    notes: "Prevents new pods from scheduling on a node"
    tiers: *reversible_write_tiers
  - tool: uncordon_node
    classification: caution
    reversible: true
    compensating_inverse: cordon_node
    notes: "Re-enables scheduling on a node"
    tiers: *reversible_write_tiers
  - tool: rollout_restart
    classification: caution
    notes: "Restarts pods in a rolling fashion — no direct inverse"
    tiers: *approval_only_tiers
  - tool: apply_configmap
    classification: caution
    notes: "Updates config — may affect running workloads"
    tiers: *approval_only_tiers
  - tool: annotate_*
    classification: caution
    tiers: *approval_only_tiers
  - tool: label_*
    classification: caution
    tiers: *approval_only_tiers

  # --- Destructive ---
  - tool: delete_pod
    classification: destructive
    notes: "Terminates a pod — will be recreated by controller if managed"
    tiers: *approval_only_tiers
  - tool: delete_deployment
    classification: destructive
    notes: "Removes an entire deployment and its pods"
    tiers: *approval_only_tiers
  - tool: delete_namespace
    classification: destructive
    notes: "Removes a namespace and ALL resources within it"
    tiers: *approval_only_tiers
  - tool: delete_*
    classification: destructive
    notes: "Catch-all for any delete operation"
    tiers: *approval_only_tiers
  - tool: drain_node
    classification: destructive
    notes: "Evicts all pods from a node"
    tiers: *approval_only_tiers
  - tool: exec_*
    deny: true
    notes: "Arbitrary command execution inside a container"
---

# Kubernetes Production — Skill Definition

This file defines explicit per-operation policies for a Kubernetes production
environment. The backend tier gate applies the active tier to each matching
operation before execution.

## Classification Guide

| Classification | Meaning | Example |
|---------------|---------|---------|
| **safe** | Read-only, no state change | `get_pods`, `describe_node` |
| **caution** | Non-destructive write, reversible | `scale_deployment`, `cordon_node` |
| **destructive** | Removes capacity or is hard to reverse | `delete_pod`, `drain_node`, `exec_*` |

## How the Agent Uses This

OpsMender uses a **3-tier AI Autonomy model** (Tier 2 is the default). The
explicit policies above combine with the selected tier at the backend tier gate:

- **Tier 2 — Advisory Only** *(default)* — analysis and recommendations only;
  operation policies use advisory or blocked modes, so remediation does not run.
- **Tier 1 — Approval Required** — read-only operations run autonomously;
  writes require operator approval; deny-listed/unknown operations are denied.
- **Tier 0 — Autonomous** *(non-prod/sandbox only — hard time limits)* — may
  execute autonomously within skill policy and the Tier 0 reversible-only floor;
  deny-listed/unknown/generic actions are still blocked.

> Tier 3 has been removed; any legacy stored `3` is remapped to Tier 2.

## Customizing

Copy this file and modify it for your environment. Add or remove tools to
match the MCP server you're using. Wildcard patterns (`*`, `?`) are supported.

## Related examples

- [`SKILL.app-incident.md`](SKILL.app-incident.md) — application-layer
  incident response: diagnose with logs/metrics/traces, file Jira
  tickets, and propose code fixes as GitHub PRs / GitLab MRs (never
  direct merges).
