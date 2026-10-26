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

- **Incident priority**: P0 / P1 / P2 / P3
- **User role**: Admin / Operator / Viewer

| Tier | Name | Behaviour |
|------|------|-----------|
| **0** | **Autonomous** | May execute remediation automatically (incl. rollbacks, restarts, failovers, and destructive ops) **but only within MCP Skill policy, deny lists, MCP permissions, and backend guardrails.** Most autonomous; **not** unlimited. Selecting Tier 0 shows a red warning. |
| **1** | **Approval Required** *(default for a new session)* | Investigates and proposes. Each operation's explicit policy chooses autonomous execution, **operator approval**, or blocking; a generic command tool always asks for **operator approval** whatever its T1 policy says, unless `allow_generic: true`; deny-listed actions never run. |
| **2** | **Advisory Only** *(workspace default)* | Analysis, recommendations, runbooks, read-only observation. **No write/remediation actions execute.** |

- **Default** for new installs (the workspace tier setting) is **Tier 2: Advisory Only**.
  The **Start Session** dialog opens on **Tier 1: Approval Required**, so a person
  approves each action unless they choose another tier (owner decision O-26).
- Operators may **override** the tier at session start (admin approval is not
  required to choose Tier 0). The selected tier is recorded on the session and
  in the audit/activity log.
- **Tier 3 is removed.** Any legacy stored value of `3` is automatically
  remapped to `2`.
- **Unknown actions are never silently allowed**: they are denied at every
  tier (Tier 2 blocks all remediation; Tier 1 denies unknown, except that a
  [generic command tool](#generic-command-tools-high-risk) asks for operator
  approval; Tier 0 denies unknown unless explicitly allowed by skill policy).

---

## Assignment: Unassigned · Global fallback · Specific tool source

Every MCP Skill has an **assignment**:

- **Specific MCP server**: applies to that server. **Takes precedence** over the
  global fallback.
- **Specific integration connector**: applies to that connector's native
  capabilities. The authored policy is intersected with the connector's
  built-in capability policy; the more restrictive decision wins.
- **Global fallback**: applies to every MCP server that has **no** specific
  skill. It does not widen native connector capability policy.
- **Unassigned**: a **saved draft**. Editable and downloadable, but **never**
  injected into AI sessions and **not** used as the global fallback. Promote it
  later by changing its assignment to Global or a specific tool source.

Resolution order for MCP tools is *server-specific → global fallback → none*.
Integration tools use their connector-bound Skill plus the immutable capability
baseline. Unassigned skills are skipped entirely.

---

## New from Template (MCP Skill Studio)

**New skill** starts from a data-driven template library: Blank, Kubernetes,
Cloud infrastructure, CI/CD & source control, or Ticketing & communications.
Each creates a structured 3-tier policy you can edit, save, and download. It
produces both:

- **Structured Markdown** with YAML front-matter `operations` (so the backend
  tier gate can classify and enforce tools), and
- **Prose sections** per tier (allow/deny/approval guidance + environment rules).

Template sections:

- **Metadata**: name, description, assignment, environment notes, owner.
- **Tier 0: Autonomous**: Allowed Actions, AI Action Workflow, Deny Actions,
  Custom Instructions.
- **Tier 1: Approval Required**: Allow List, Ask Approval, Deny List, Custom
  Instructions.
- **Tier 2: Advisory Only**: "Advisory mode: reads only." (safe reads the skill
  marks autonomous at T2 may run; nothing else) + Custom Instructions.
- **Environment Rules (optional)**: per-environment expectations when one MCP
  server spans multiple environments.

### Free-form custom instructions

Text written under a tier's **Custom Instructions** heading is active runtime
guidance. OpsMender preserves its Markdown and supplies **only the selected
session tier's instructions** to the model during observation, diagnosis,
planning, verification, and summarization. This is useful for local operating
conventions such as investigation order, naming rules, evidence requirements,
rollback expectations, and report format.

```markdown
## Tier 1: Approval Required

### Custom Instructions

- Check for an existing incident ticket before creating one.
- Show the exact target, impact, and rollback before requesting approval.
- Prefix new ticket titles with `[OpsMender]`.
```

Custom instructions guide model reasoning; they are **not permissions**. They
cannot make an unavailable tool available, change the active tier, override a
deny entry, bypass approval, or relax the backend's structured operation policy.
Unassigned draft skills still never enter a session.

A session also receives the active tier's Custom Instructions from each
integration connector on the service's allowlist that has a bound Skill. They
follow the MCP Skill's instructions, one labelled section per connector,
ordered by connector name. A connector Skill that can't be read adds no
instructions (its tools stay denied), and a disabled or unlisted connector adds
none. Only the MCP Skill's AI Action Workflow is used; connector workflows are
not merged.

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

Deny lists should be **explicit**: list the exact identifiers (or glob patterns
like `delete_*`) the AI must never execute.

---

## Environment patterns

### Pattern 1: separate MCP servers per environment

```text
aws-prod-mcp     → strict skill: Tier 0 heavily restricted, destructive denied
aws-staging-mcp  → more remediation: restarts allowed
aws-dev-mcp      → broad autonomous remediation acceptable
```

Create a **separate MCP Skill per server** and tune its tiers.

### Pattern 2: one MCP server, multiple environments

```text
aws-multi-env-mcp → one skill with an Environment Rules section
```

Use the **Environment Rules** section to describe per-environment expectations.
These are examples only: define your own environments; nothing in the backend
hardcodes prod/staging/dev.

---

## Generic command tools (high-risk)

Generic execution tools run **arbitrary** commands, so the tool name alone does
not bound what they can do. OpsMender detects them automatically by the names
below and guards them conservatively. Names are compared without case and with
camel case, hyphens, dots and spaces folded to underscores, so
`ExecuteCommand`, `run-command`, `kubectl-exec`, `Terminal`, `ShellExec` and
`bash-tool` count too:

- **Tier 2**: blocked.
- **Tier 1**: **requires operator approval** before execution, and runs once
  approved.
- **Tier 0**: blocked (there is no command-pattern allowlisting yet, so a
  generic tool cannot run autonomously).

The guardrail is checked after the deny-list and **before the tool's own tier
policy**, so at Tier 1 a generic tool asks for operator approval, and runs once
approved, even when its entry sets T1 to `blocked` or `advisory` or the skill
does not list it. A `blocked` T1 policy is not enough to stop it: give it a
`deny: true` entry, which is checked first and blocks it at every tier. This
also holds for names OpsMender has started detecting, such as `terminal`,
`sudo` and `docker`: where a skill blocked one at Tier 1 before it was
detected, Tier 1 sessions now ask for approval to run it until the tool is
deny-listed.

| Kind | Detected names |
| --- | --- |
| Shells and runners | `shell`, `bash`, `sh`, `zsh`, `fish`, `powershell`, `pwsh`, `cmd`, `shellexec`, `bash_tool`, `shell_tool`, `invoke_expression`, `run`, `run_command`, `runcommand`, `run_cmd`, `exec`, `execute`, `command`, `eval`, `evaluate`, `spawn`, `system` |
| Terminals, processes and privilege | `terminal`, `console`, `tty`, `pty`, `subprocess`, `popen`, `os_system`, `sudo`, `su` |
| Containers and host services | `docker`, `podman`, `nerdctl`, `crictl`, `systemctl` |
| Cloud and infrastructure CLIs | `kubectl`, `oc`, `helm`, `aws`, `aws_cli`, `awscli`, `gcloud`, `az`, `azure_cli`, `azurecli`, `terraform`, `tf`, `pulumi`, `ansible`, `ansible_playbook`, `salt`, `chef`, `puppet` |
| Database and query runners | `sql`, `psql`, `mysql`, `sqlcmd`, `mongo`, `redis_cli`, `query`, `run_query`, `execute_sql`, `exec_sql`, `raw_query` |
| Language runtimes | `python`, `python3`, `node`, `nodejs`, `ruby`, `perl`, `php`, `groovy`, `jshell`, `repl`, `python_repl`, `code_interpreter`, `interpreter` |
| Network and transfer | `curl`, `wget`, `ssh`, `scp`, `sftp`, `telnet`, `nc`, `netcat` |
| Build and package | `make`, `npm`, `npx`, `pip`, `apt`, `yum`, `apk` |
| Runner tools of common MCP servers | `kubectl_generic`, `call_kubectl`, `call_az`, `call_aws`, `call_helm`, `call_cilium`, `call_hubble`, `execute_kubectl`, `execute_helm`, `execute_istioctl`, `execute_argocd`, `aws_cli_pipeline`, `kubernetes_proxy`, `ssh_execute`, `ssh_execute_sudo`, `ssh_execute_group`, `ssh_session_send`, `start_process`, `interact_with_process`, `powershell_tool`, `execute_code`, `execute_code_file`, `execute_python`, `execute_cell`, `insert_execute_code_cell`, `execute_ipython`, `execute_tool`, `use_tool`, `browser_run_code`, `browser_run_code_unsafe`, `deploy_one_off_dyno`, `ansible_task`, `transact`, `pg_psql`, `apply_migration`, `execute_sql_readonly`, `explain_sql_statement`, `generic_open_search_api_tool` |
| Names starting with | `exec_`, `run_`, `shell_`, `cmd_`, `eval_`, `bash_`, `powershell_`, `power_shell_`, `pwsh_`, `sh_`, `terminal_`, `console_`, `sudo_`, `docker_`, `subprocess_` |
| Names ending with | `_exec`, `_shell`, `_command`, `_cmd`, `_cli`, `_eval`, `_query`, `_run`, `_script`, `_repl`, `_interpreter`, `_terminal`, `_console`, `_bash`, `_powershell`, `_pwsh`, `_subprocess`, `_cypher` |

A name counts as a whole or by its start or end, never by a word in the
middle: `refresh`, `dashboard` and `get_console_output` are ordinary tools.

Some tools are bounded by design but still powerful, so no name rule catches
them: manifest and chart appliers (`apply_yaml`, `resources_create_or_update`,
`kubectl_apply`, `install_helm_chart`), multi-operation job tools
(`manage_aws_emr_ec2_steps`, `create_pyspark_batch`,
`prepare_database_migration`), single router tools that reach a whole
service (`azure`, `postgres`, `kusto`), page scripting
(`browser_evaluate`) and tool names with a server prefix (`aws___call_aws`).
Classify these in the Skill: deny them, or give them a destructive class and
the tier policies you want.

To opt a **narrowly-scoped** wrapper out of the guardrail, list it **by its
exact name** in the skill with `allow_generic: true`: its own tier policies
then apply as written, a T1 `blocked` or `advisory` included. A glob such as
`run_*` can't opt tools out. Prefer explicit `deny: true` entries for anything
dangerous. A few prefixes and suffixes also catch bounded tools: `docker_logs`
and `console_url` start with `docker_` and `console_`, and `get_console` and
`aws_console` end with `_console`, so they stay guarded until their exact
entry sets `allow_generic: true`.

```yaml
operations:
  - tool: shell
    deny: true              # blocked at every tier (deny always wins)
  - tool: docker_logs       # only reads container logs; docker_ flags it
    classification: safe
    allow_generic: true
    tiers:
      T0: {enabled: true, mode: autonomous, require_reversible: true}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: advisory}
```

## Action classification & deny lists

Each tool carries a classification for risk labeling: `safe` (read-only /
low-risk) · `caution` (reversible writes) · `destructive` (high-risk /
irreversible) · `unknown` (unclassified, **always denied**, never silently
allowed) · generic-execution (auto-detected). Its explicit T0/T1/T2 policy
controls execution. An entry with `deny: true` is
blocked at **every tier: deny always wins**, even over `allow_generic` or a
`safe` classification, and even at Tier 0. It wins wherever it appears: a deny
entry below a broad glob such as `*` still blocks the tools it matches. An
exact entry can't carve an exception out of a deny glob either; to allow one
tool, narrow the glob.

When several globs match a tool, the strictest one sets its policy, whatever
order they are written in: a broad `delete_*` allowed with approval can't let
through a tool that a narrower `delete_prod_*` blocks. Strictness goes by
`deny`, then classification (`destructive` over `caution` over `safe`), then
the strictest tier mode (blocked or disabled, advisory, approval, autonomous),
then irreversibility. An entry with the tool's exact name still decides for
that tool.

Conservative defaults: if **no skill** resolves for a server (no server-specific
and no global), unclassified write/remediation actions are treated as unknown
and **denied**.

## Explicit per-operation tier policy

Every executable operation must declare complete `tiers` entries for T0, T1,
and T2. The structured policy is the source of truth enforced by
`backend/tiers/enforcement.py`:

- `mode: autonomous` permits execution without an operator ACK.
- `mode: approval` routes the action through the approval gate.
- `mode: blocked` denies execution. At Tier 1 a generic command tool (see
  below) asks for approval instead, unless the Skill sets `allow_generic: true`
  for it or deny-lists it.
- `mode: advisory` allows guidance but no execution.
- `require_reversible: true` keeps the Tier 0 reversible floor. Safe reads are
  implicitly reversible; side-effecting operations also need
  `reversible: true` and a `compensating_inverse`.
- `require_reversible: false` explicitly allows a T0 operation to bypass that
  reversible floor. Deny and generic-command guardrails still win.

Classification-only skills are converted on import to a conservative
compatibility policy: T0 autonomous behind the reversible floor, T1 approval,
and T2 advisory. Stored skills are migrated the same way. A conversion notice
is returned so the generated policy can be reviewed. Partial or invalid explicit
policies are rejected rather than inferred.

```yaml
---
version: "1"
environment: production
default_tier: T2

operations:
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
---
# Skill Guidance

## Tier 0: Autonomous
Use only explicitly permitted tools. Execute autonomous actions only when the
structured operation policy allows it.

## Tier 1: Approval Required
Investigate and prepare remediation. Pause for approval before caution or
destructive actions.

## Tier 2: Advisory
Observe, diagnose, and explain what should be done. Do not execute write actions.
```

## Backend enforcement (hard safety)

> **What is guaranteed.** OpsMender prevents *execution* beyond the selected tier
> and MCP Skill policy through backend enforcement. The model can still *suggest*
> or *describe* an unsafe action in text: the guarantee is execution safety, not
> perfect model reasoning. A prompt-injected "ignore policy and delete prod" is
> blocked at the tier gate and never reaches MCP execution.

Enforcement order is fixed: deny entries always win, then the generic-command
guardrail applies, then the operation's explicit active-tier policy, followed by
the Tier 0 reversible floor. Unknown or underspecified operations are denied,
except a generic command tool at Tier 1, which asks for approval.

The tier gate runs before any tool/action execution and knows: the selected
session tier, the skill policy for the selected MCP server, the action's
classification, whether approval is required, and whether the action is denied.

- **Tier 2**: an operation classified `safe` may be `autonomous`, so a Tier 2
  session can read health, logs and metrics before it advises; every other
  operation must be advisory or blocked, approval never applies at Tier 2, a
  generic command tool stays blocked, and blocked actions are logged. Skills
  generated by OpsMender give safe operations `T2: {enabled: true, mode:
  autonomous}`; a stored skill keeps its T2 policies until you change them.
- **Tier 1**: each operation explicitly chooses autonomous or approval mode;
  deny-listed and unknown actions are blocked; decisions are logged. A generic
  command tool without `allow_generic` is the exception: it asks for approval
  whatever its own policy says, unless it is deny-listed.
- **Tier 0**: executes only actions explicitly permitted by skill policy and
  not deny-listed. Explicit policy may set `require_reversible: false`; otherwise
  the reversible sandbox floor applies. Unknown actions are blocked and every
  decision is logged.

Every MCP tool call flows through one chokepoint (`audited_tool_call` →
`tier_check`), and the live-rollback path is gated by the Tier 0 sandbox
allowlist.

## Generate from tools (Skill Studio)

**Generate from tools** builds a skill draft from a real MCP server or native
integration connector:

1. **Pick a tool source** and click **Discover tools**: OpsMender connects to
   the saved MCP server or loads the integration connector's capability
   descriptors.
2. **Review the suggestions.** Each tool gets a heuristic starting
   classification from its name: read verbs (`get`/`list`/`describe`…) → `safe`;
   destructive verbs (`delete`/`destroy`/`drop`…) → `destructive`; reversible
   writes (`restart`/`scale`/`update`…) → `caution`. **Generic command tools**
   (`shell`, `kubectl`, `run_command`, …) are flagged `generic` and suggested
   **deny**; anything unrecognized defaults to `caution` and is flagged for
   review (never silently `safe`).
3. **(Optional) AI assist**: type a freeform **intent** (e.g. "production
   Kubernetes: be conservative, never auto-delete; restarts OK if health checks
   pass") and click **AI assist**. OpsMender asks your configured model to
   classify each tool and author per-tier instructions, seeded by that intent.
4. **Override anything**: change the classification, toggle deny, opt a scoped
   generic wrapper out with `allow_generic`, add notes, and write/adjust per-tier
   custom instructions.
5. **(For autonomous tools) set Tier 0 safety metadata.** Tick **Tier 0
   (autonomous)** on a tool you want the AI to run without approval. For a
   non-`safe` tool this reveals **Reversible?** and **Compensating inverse**
   (the rollback tool). The backend Tier 0 safety floor only lets a non-`safe`
   action run autonomously when it is `reversible: true` **and** has a
   `compensating_inverse`, so generation is blocked until you provide both (or
   untick Tier 0, leaving the tool at Tier 1 approval). Read-only `safe` tools
   clear Tier 0 automatically.
6. **Generate draft**: OpsMender deterministically builds a structured 3-tier
   skill (YAML `operations` front-matter + prose) and opens it in the editor.
7. **Review, edit, then save** (Unassigned by default) or **download**.

The editor validates through the same backend parser used at runtime. Errors
include line numbers where possible and block save. The operation table shows
the parsed policy, bound-source coverage, and the invariant that unclassified
tools are denied at every tier. Generated changes include a before/after policy
diff; risk reclassification or permission widening is called out for review.

> **Tier 0 autonomous actions require explicit safety metadata**:
> reversibility and a compensating inverse / rollback. Skill Studio helps you
> collect it, but the backend tier gate remains authoritative: a Tier 0 action
> without the required metadata is blocked at runtime regardless of what the
> skill says, and Tier 0 never runs arbitrary destructive actions.

> The default suggestions are heuristic and the draft is built **deterministically**
> from your reviewed classifications. The generated front-matter is validated by
> the same parser the tier gate uses. The backend tier gate, deny lists, the
> generic-command guardrail, and conservative unknown-deny defaults remain the
> execution authority: a generated skill never relaxes them.

### AI assist (optional)

When a model is configured, **AI assist** uses it to *suggest* classifications and
write per-tier guidance from your intent prompt. It is strictly an assist:

- You review and override every row before generating.
- **Generic command tools** (`shell`, `kubectl`, `run_command`, …) are
  **force-denied regardless of what the model says**: the model can never relax
  the generic-command guardrail.
- Any model classification *less restrictive* than OpsMender's own heuristic is
  flagged **needs-review** so you notice the downgrade.
- The model may propose **Tier 0 safety metadata** (`reversible` + a
  `compensating_inverse`) for autonomous tools, but an autonomous **destructive**
  inverse, and any reversible tool the model could not give an inverse for, is
  flagged **needs-review**. It can never invent an inverse that relaxes the Tier 0
  floor.
- If no model is configured (or the provider is unreachable), AI assist is
  unavailable and the Studio falls back to the heuristic suggestions.

The model helps *author* the skill; it never changes what the tier gate enforces.
