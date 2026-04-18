---
version: "1"
environment: kubernetes-production
operations:
  # --- Safe (read-only) ---
  - tool: get_pods
    classification: safe
  - tool: get_pod_logs
    classification: safe
  - tool: get_namespaces
    classification: safe
  - tool: get_deployments
    classification: safe
  - tool: get_services
    classification: safe
  - tool: get_events
    classification: safe
  - tool: get_nodes
    classification: safe
  - tool: describe_*
    classification: safe
    notes: "All describe operations are read-only"
  - tool: list_*
    classification: safe
    notes: "All list operations are read-only"

  # --- Caution (non-destructive writes) ---
  # `reversible: true` is the Tier 0 safety floor — only reversible ops run
  # autonomously at Tier 0.  `compensating_inverse` names the tool the
  # rollback engine invokes to undo this one (same parameters).
  - tool: scale_deployment
    classification: caution
    reversible: true
    notes: "Changes replica count — reversible but impacts capacity (inverse is parametric, not auto-rolled-back)"
  - tool: cordon_node
    classification: caution
    reversible: true
    compensating_inverse: uncordon_node
    notes: "Prevents new pods from scheduling on a node"
  - tool: uncordon_node
    classification: caution
    reversible: true
    compensating_inverse: cordon_node
    notes: "Re-enables scheduling on a node"
  - tool: rollout_restart
    classification: caution
    notes: "Restarts pods in a rolling fashion — no direct inverse"
  - tool: apply_configmap
    classification: caution
    notes: "Updates config — may affect running workloads"
  - tool: annotate_*
    classification: caution
  - tool: label_*
    classification: caution

  # --- Destructive ---
  - tool: delete_pod
    classification: destructive
    notes: "Terminates a pod — will be recreated by controller if managed"
  - tool: delete_deployment
    classification: destructive
    notes: "Removes an entire deployment and its pods"
  - tool: delete_namespace
    classification: destructive
    notes: "Removes a namespace and ALL resources within it"
  - tool: delete_*
    classification: destructive
    notes: "Catch-all for any delete operation"
  - tool: drain_node
    classification: destructive
    notes: "Evicts all pods from a node"
  - tool: exec_*
    classification: destructive
    notes: "Arbitrary command execution inside a container"
---

# Kubernetes Production — Skill Definition

This file defines operation classifications for a Kubernetes production
environment. The AI Incident Manager agent uses these classifications along
with the active tier to decide what it can do autonomously.

## Classification Guide

| Classification | Meaning | Example |
|---------------|---------|---------|
| **safe** | Read-only, no state change | `get_pods`, `describe_node` |
| **caution** | Non-destructive write, reversible | `scale_deployment`, `cordon_node` |
| **destructive** | Removes capacity or is hard to reverse | `delete_pod`, `drain_node`, `exec_*` |

## How the Agent Uses This

- **Tier 2** — executes `safe` + `caution` autonomously, surfaces `destructive` as recommendations
- **Tier 3** — advises only, human executes everything
- **Tier 1** — executes everything, but `destructive` requires human approval first
- **Tier 0** — full autonomous (non-prod/sandbox only)

## Customizing

Copy this file and modify it for your environment. Add or remove tools to
match the MCP server you're using. Wildcard patterns (`*`, `?`) are supported.
