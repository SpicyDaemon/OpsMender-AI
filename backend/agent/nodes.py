"""Workflow node functions for the incident response graph.

Each function has the signature ``(state: IncidentState) -> dict``
and returns a partial state update.  Nodes that need an LLM are built
via factory functions (closures) that capture the LLM instance, keeping
the node signature compatible with LangGraph.

Node order (from REFERENCE.md)
------------------------------
observe → diagnose → plan → tier_gate → execute → verify → summarize

Design rules
-------------
* ``tier_gate`` is a **hard programmatic check** — NOT an LLM decision.
  It reads the plan, classifies each proposed action via the skill
  definition, and splits them into approved / blocked lists.
* LLM-powered nodes receive the LLM via closure injection so the node
  function itself remains ``(state) -> dict``.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.agent.llm import LLM
from backend.agent.state import IncidentState
from backend.approvals import ApprovalService
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import check as tier_check


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

OBSERVE_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) performing incident response.

An incident has been reported with the following description:

---
{incident_description}
---

Based on this description, provide a structured summary of:
1. What is known so far
2. What systems or components appear to be affected
3. What initial observations can be made
4. What information would be useful to gather next

Be concise and actionable.  Focus on facts, not speculation."""


DIAGNOSE_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) diagnosing an incident.

Here are the observations gathered so far:

---
{observations}
---

Based on these observations, provide:
1. A root cause analysis (what is most likely causing the issue)
2. Contributing factors
3. Severity assessment (critical / high / medium / low)
4. Confidence level in the diagnosis (high / medium / low)

Be concise and structured."""


PLAN_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) planning remediation.

Diagnosis:
---
{diagnosis}
---

Available tools (from skill definition):
{available_tools}

Preferred MCP servers for this incident:
{preferred_mcp_servers}

Operator guidance (the operator redirected your previous plan — follow this):
{operator_guidance}

Current tier: {tier} (determines what actions are allowed)

Propose a list of remediation actions.  For each action, provide:
- tool_name: the MCP tool to call
- tool_parameters: a dict of parameters
- justification: why this action is needed

Return your plan as a JSON array of objects.  Example:
[
  {{"tool_name": "get_pods", "tool_parameters": {{"namespace": "default"}}, "justification": "Check pod status"}}
]

Only propose actions using available tools.  Be conservative — prefer
safe read operations before any writes."""


VERIFY_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) verifying incident remediation.

Diagnosis: {diagnosis}
Actions executed: {tool_call_count}
Results:
---
{tool_call_results}
---

Based on the results, assess:
1. Were the actions successful?
2. Is the incident resolved?
3. Are there any remaining concerns?
4. What follow-up actions (if any) are recommended?

Be concise and definitive."""


SUMMARIZE_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) writing an incident summary.

Incident description: {incident_description}
Diagnosis: {diagnosis}
Verification: {verification}
Actions taken: {tool_call_count}
Blocked actions: {blocked_count}

Write a concise incident summary covering:
1. What happened
2. Root cause
3. Actions taken
4. Current status
5. Follow-up items (if any)

Keep it under 200 words."""


AGENT_ROLE_SPECS: dict[str, dict[str, str]] = {
    "incident_commander": {
        "label": "Incident Commander",
        "focus": (
            "Prioritize business impact, operator clarity, decision-making, "
            "and the most important next steps."
        ),
    },
    "investigator": {
        "label": "Investigator",
        "focus": (
            "Prioritize concrete evidence, failure domains, plausible root causes, "
            "and what data should be gathered next."
        ),
    },
    "skeptic": {
        "label": "Skeptic",
        "focus": (
            "Challenge assumptions, call out uncertainty, identify alternate "
            "hypotheses, and highlight missing evidence or risky leaps."
        ),
    },
    "remediator": {
        "label": "Remediator",
        "focus": (
            "Prioritize low-risk remediation sequencing, rollback awareness, "
            "and safe operational execution."
        ),
    },
}


MULTI_AGENT_ROLE_PROMPT = """\
You are acting as the {role_label} in a multi-agent incident response team.

Role focus:
{role_focus}

Complete the following task from that perspective. Keep the answer grounded,
conservative, and directly usable by operators.

Task:
---
{task_prompt}
---

Return only the answer requested by the task."""


MULTI_AGENT_SYNTHESIS_PROMPT = """\
You are consolidating outputs from a multi-agent incident response team.

Original task:
---
{task_prompt}
---

Role outputs:
---
{role_outputs}
---

Produce one final consolidated answer that preserves the output format requested
by the original task. Reconcile conflicts conservatively and surface uncertainty
when relevant."""


def validate_agent_roles(agent_roles: list[str] | None) -> list[str]:
    if agent_roles is None:
        return []
    if not agent_roles:
        return []

    cleaned = [str(role).strip() for role in agent_roles if str(role).strip()]
    if not cleaned:
        raise ValueError("Agent team roles cannot be empty")
    if len(cleaned) != len(agent_roles):
        raise ValueError("Agent team roles cannot contain blank role names")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Agent team roles cannot contain duplicate roles")

    invalid = [role for role in cleaned if role not in AGENT_ROLE_SPECS]
    if invalid:
        raise ValueError(f"Unsupported agent roles: {', '.join(invalid)}")

    return cleaned


def _invoke_multi_agent(llm: LLM, *, task_prompt: str, agent_roles: list[str]) -> str:
    if not agent_roles:
        return llm.invoke(task_prompt)

    role_outputs: list[str] = []
    for role in agent_roles:
        role_spec = AGENT_ROLE_SPECS[role]
        role_prompt = MULTI_AGENT_ROLE_PROMPT.format(
            role_label=role_spec["label"],
            role_focus=role_spec["focus"],
            task_prompt=task_prompt,
        )
        role_response = llm.invoke(role_prompt)
        role_outputs.append(f"{role_spec['label']}:\n{role_response}")

    synthesis_prompt = MULTI_AGENT_SYNTHESIS_PROMPT.format(
        task_prompt=task_prompt,
        role_outputs="\n\n".join(role_outputs),
    )
    return llm.invoke(synthesis_prompt)


# ---------------------------------------------------------------------------
# recall (Sprint 45)
# ---------------------------------------------------------------------------


def _build_recall(
    memory_factory: Any,
    *,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
):
    """Return a recall node closed over the org + service binding.

    The closure performs no LLM call — it's a pure SQL lookup against the
    ``incident_memories`` table. Surfaced memories are written to
    ``incident_memory_recall_log`` and stamped via ``last_used_at`` so the
    self-improvement signal stays accurate.

    The node always succeeds. Memory is advisory; any retrieval failure is
    swallowed inside :func:`backend.memory.retrieval.recall_for_session` so
    the session keeps moving.
    """

    # Local import to avoid pulling DB code into stub / CLI test paths that
    # never enable memory.
    from backend.memory.retrieval import recall_for_session

    async def recall(state: IncidentState) -> dict:
        session_id_raw = state.get("session_id")
        if not session_id_raw:
            return {}
        try:
            session_id = uuid.UUID(str(session_id_raw))
        except (TypeError, ValueError):
            return {}

        incident = state.get("incident") or {}
        result = await recall_for_session(
            memory_factory,
            org_id=org_id,
            session_id=session_id,
            service_id=service_id,
            incident=dict(incident),
        )
        if result.is_empty:
            return {}
        return {
            "memory_context": result.context_block,
            "recalled_memory_ids": list(result.memory_ids),
        }

    return recall


def recall(state: IncidentState) -> dict:
    """Stub recall node (no memory). Use ``_build_recall`` for real logic."""
    return {}


# ---------------------------------------------------------------------------
# observe
# ---------------------------------------------------------------------------

OBSERVE_PROMPT_WITH_MEMORY = """\
You are an expert Site Reliability Engineer (SRE) performing incident response.

{memory_context}\
An incident has been reported with the following description:

---
{incident_description}
---

Based on this description, provide a structured summary of:
1. What is known so far
2. What systems or components appear to be affected
3. What initial observations can be made
4. What information would be useful to gather next

Be concise and actionable.  Focus on facts, not speculation."""


def _build_observe(llm: LLM, agent_roles: list[str] | None = None):
    """Return an observe node function closed over the LLM instance."""

    agent_roles = validate_agent_roles(agent_roles)

    def observe(state: IncidentState) -> dict:
        """Gather initial observations about the incident.

        Sends the incident description to the LLM for structured
        analysis and initial observation gathering. When memory recall
        produced a context block (Sprint 45), it is prepended to the
        prompt so prior lessons inform the first pass.
        """
        description = state.get("incident_description", "")
        memory_context = state.get("memory_context", "")
        if memory_context:
            # Trailing blank line so the memory block reads cleanly above
            # the next paragraph.
            memory_block = memory_context.rstrip() + "\n\n"
            prompt = OBSERVE_PROMPT_WITH_MEMORY.format(
                memory_context=memory_block,
                incident_description=description,
            )
        else:
            prompt = OBSERVE_PROMPT.format(incident_description=description)
        observations = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )
        return {
            "observations": observations,
            "status": "active",
        }

    return observe


# Keep the simple version for backward compatibility / direct testing
def observe(state: IncidentState) -> dict:
    """Stub observe node (no LLM).  Use ``_build_observe`` for real logic."""
    description = state.get("incident_description", "")
    return {
        "observations": f"[observe] Gathered observations for: {description}",
        "status": "active",
    }


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

def _build_diagnose(llm: LLM, agent_roles: list[str] | None = None):
    """Return a diagnose node function closed over the LLM instance."""

    agent_roles = validate_agent_roles(agent_roles)

    def diagnose(state: IncidentState) -> dict:
        """Analyse observations and produce a diagnosis."""
        observations = state.get("observations", "")
        prompt = DIAGNOSE_PROMPT.format(observations=observations)
        diagnosis = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )
        return {
            "diagnosis": diagnosis,
        }

    return diagnose


def diagnose(state: IncidentState) -> dict:
    """Stub diagnose node (no LLM).  Use ``_build_diagnose`` for real logic."""
    observations = state.get("observations", "")
    return {
        "diagnosis": f"[diagnose] Analysis of: {observations}",
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

# Defensive bound on the Tier 1 interactive redirect loop. Each redirect loops
# plan -> tier_gate once; this caps how many times the operator can redirect a
# single session before the gate stops looping (the graph recursion_limit is
# set comfortably above this in the session runner).
MAX_TIER1_REDIRECTS = 15


def _format_operator_guidance(state: IncidentState) -> str:
    """Render accumulated operator redirect guidance for the plan prompt."""
    guidance = state.get("operator_guidance", []) or []
    if not guidance:
        return "(none — this is your first plan for the incident)"
    return "\n".join(f"- {item}" for item in guidance)


def _build_plan(
    llm: LLM,
    tier: int,
    skill_def: SkillDefinition,
    agent_roles: list[str] | None = None,
):
    """Return a plan node function closed over the LLM, tier, and skill def."""

    agent_roles = validate_agent_roles(agent_roles)

    def plan(state: IncidentState) -> dict:
        """Propose a list of remediation actions."""
        import json

        diagnosis = state.get("diagnosis", "")
        tools_list = "\n".join(
            f"- {op.tool} ({op.classification})"
            for op in skill_def.operations
        )
        prompt = PLAN_PROMPT.format(
            diagnosis=diagnosis,
            available_tools=tools_list,
            preferred_mcp_servers="\n".join(
                f"- {name}" for name in state.get("preferred_mcp_servers", [])
            )
            or "(none configured)",
            operator_guidance=_format_operator_guidance(state),
            tier=tier,
        )
        raw = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )

        # Parse the LLM response as JSON — fall back to empty plan on failure
        try:
            actions = json.loads(raw)
            if not isinstance(actions, list):
                actions = []
        except (json.JSONDecodeError, TypeError):
            actions = []

        return {
            "plan": actions,
        }

    return plan


def _build_plan_with_tool_names(
    llm: LLM,
    tier: int,
    skill_def: SkillDefinition,
    tool_names: list[str],
    agent_roles: list[str] | None = None,
    tool_descriptions: dict[str, str] | None = None,
):
    """Return a plan node that only exposes the supplied concrete tools."""

    agent_roles = validate_agent_roles(agent_roles)

    def plan(state: IncidentState) -> dict:
        import json

        diagnosis = state.get("diagnosis", "")
        if tool_names:
            tools_list = "\n".join(
                (
                    f"- {tool_name} ({skill_def.classify(tool_name)}): "
                    f"{tool_descriptions[tool_name]}"
                    if tool_descriptions and tool_name in tool_descriptions
                    else f"- {tool_name} ({skill_def.classify(tool_name)})"
                )
                for tool_name in tool_names
            )
        else:
            tools_list = "(none available)"
        prompt = PLAN_PROMPT.format(
            diagnosis=diagnosis,
            available_tools=tools_list,
            preferred_mcp_servers="\n".join(
                f"- {name}" for name in state.get("preferred_mcp_servers", [])
            )
            or "(none configured)",
            operator_guidance=_format_operator_guidance(state),
            tier=tier,
        )
        raw = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )

        try:
            actions = json.loads(raw)
            if not isinstance(actions, list):
                actions = []
        except (json.JSONDecodeError, TypeError):
            actions = []

        return {
            "plan": actions,
        }

    return plan


def plan(state: IncidentState) -> dict:
    """Stub plan node (no LLM).  Use ``_build_plan`` for real logic."""
    return {
        "plan": [],
    }


# ---------------------------------------------------------------------------
# tier_gate  (HARD PROGRAMMATIC CHECK — not an LLM decision)
# ---------------------------------------------------------------------------

def _tier_block_reason(status: str) -> str:
    if status == "rejected":
        return "Approval rejected by human operator"
    if status == "expired":
        return "Approval timed out before human response"
    return "Action blocked by tier policy"


def _build_tier_gate(
    tier: int,
    skill_def: SkillDefinition,
    approval_service: ApprovalService | None = None,
):
    """Return a tier_gate node function closed over *tier* and *skill_def*.

    The tier gate is deterministic: it reads the proposed actions from
    ``state["plan"]``, classifies each via the skill definition, and
    splits them into ``approved_actions`` and ``blocked_actions``.

    This function is **never** delegated to an LLM.
    """

    if approval_service is None:

        def tier_gate(state: IncidentState) -> dict:
            proposed = state.get("plan", [])
            approved: list[dict[str, Any]] = []
            blocked: list[dict[str, Any]] = []

            for action in proposed:
                tool_name = action.get("tool_name", "")
                enforcement = tier_check(tool_name, tier, skill_def)
                if enforcement.requires_approval:
                    blocked.append({
                        **action,
                        "block_reason": (
                            f"{enforcement.classification} action requires an "
                            "approval service (none configured)"
                        ),
                        "classification": enforcement.classification,
                    })
                    continue

                if enforcement.permitted:
                    approved.append(action)
                else:
                    blocked.append({
                        **action,
                        "block_reason": enforcement.reason,
                        "classification": enforcement.classification,
                    })

            return {
                "approved_actions": approved,
                "blocked_actions": blocked,
                "approval_requests": [],
            }

        return tier_gate

    async def tier_gate(state: IncidentState) -> dict:
        proposed = state.get("plan", [])
        approved: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        approval_requests: list[dict[str, Any]] = []
        status = state.get("status")
        error = state.get("error")
        session_id = uuid.UUID(state["session_id"])
        redirect_count = int(state.get("redirect_count", 0) or 0)

        for action in proposed:
            tool_name = action.get("tool_name", "")
            enforcement = tier_check(tool_name, tier, skill_def)

            if enforcement.requires_approval:
                resolution = await approval_service.request_and_wait(
                    session_id=session_id,
                    action=action,
                    justification=action.get("justification"),
                )
                req_status = resolution.request.status
                approval_requests.append({
                    "request_id": str(resolution.request.id),
                    "status": req_status,
                    "action": resolution.request.action,
                    "expires_at": resolution.request.expires_at.isoformat(),
                })
                if req_status == "approved":
                    approved.append({
                        **action,
                        "approval_request_id": str(resolution.request.id),
                    })
                elif req_status == "redirected" and redirect_count < MAX_TIER1_REDIRECTS:
                    # Operator steered the AI. Abandon the rest of this plan
                    # pass and loop back to the plan node with the guidance in
                    # context — the conditional edge after tier_gate routes on
                    # ``redirect_requested``. Already-approved actions from this
                    # same pass are intentionally dropped (the operator chose a
                    # different course of action).
                    guidance = (resolution.guidance or "").strip()
                    update: dict[str, Any] = {
                        "approved_actions": [],
                        "blocked_actions": [],
                        "approval_requests": approval_requests,
                        "redirect_requested": True,
                        "redirect_count": redirect_count + 1,
                        "status": status,
                        "error": error,
                    }
                    if guidance:
                        update["operator_guidance"] = [guidance]
                    return update
                else:
                    # rejected, expired, or a redirect past the loop cap.
                    block_reason = resolution.block_reason or _tier_block_reason(
                        req_status
                    )
                    if req_status == "redirected":
                        block_reason = (
                            f"Maximum Tier 1 redirects reached "
                            f"({MAX_TIER1_REDIRECTS}); redirect loop stopped."
                        )
                    blocked.append({
                        **action,
                        "block_reason": block_reason,
                        "classification": enforcement.classification,
                        "approval_request_id": str(resolution.request.id),
                    })
                    if req_status == "expired":
                        status = "timed_out"
                        error = resolution.block_reason
                continue

            if enforcement.permitted:
                approved.append(action)
            else:
                blocked.append({
                    **action,
                    "block_reason": enforcement.reason,
                    "classification": enforcement.classification,
                })

        return {
            "approved_actions": approved,
            "blocked_actions": blocked,
            "approval_requests": approval_requests,
            "redirect_requested": False,
            "redirect_count": redirect_count,
            "status": status,
            "error": error,
        }

    return tier_gate


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

def _build_execute(
    mcp_session,
    skill_def: SkillDefinition,
    audit_logger,
    tool_caller=None,
):
    """Return an execute node that calls ``audited_tool_call`` for each
    approved action.

    Parameters
    ----------
    mcp_session:
        Active MCP ``ClientSession``.
    skill_def:
        Loaded skill definition (passed through to ``audited_tool_call``).
    audit_logger:
        ``AuditLogger`` instance for recording tool calls.
    """
    from backend.audit.executor import audited_tool_call

    async def execute(state: IncidentState) -> dict:
        """Execute approved actions via the audited MCP tool-call wrapper."""
        approved = state.get("approved_actions", [])
        session_id = state.get("session_id", "unknown")
        tier = state.get("tier", 2)

        records: list[dict[str, Any]] = []
        for action in approved:
            tool_name = action.get("tool_name", "")
            tool_params = action.get("tool_parameters", {})

            result = await audited_tool_call(
                session=mcp_session,
                tool_name=tool_name,
                tool_parameters=tool_params,
                session_id=session_id,
                tier=tier,
                skill_def=skill_def,
                logger=audit_logger,
                tool_caller=tool_caller,
            )

            records.append({
                "tool_name": tool_name,
                "tool_parameters": tool_params,
                "classification": result.enforcement.classification,
                "permitted": result.permitted,
                "result": result.result,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "block_reason": None,
            })

        return {"tool_calls": records}

    return execute


def execute(state: IncidentState) -> dict:
    """Stub execute node (no MCP session).

    Use ``_build_execute`` when an MCP session and audit logger are
    available.
    """
    return {
        "tool_calls": [],
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _build_verify(llm: LLM, agent_roles: list[str] | None = None):
    """Return a verify node function closed over the LLM instance."""

    agent_roles = validate_agent_roles(agent_roles)

    def verify(state: IncidentState) -> dict:
        """Verify the results of executed actions."""
        diagnosis = state.get("diagnosis", "")
        tool_calls = state.get("tool_calls", [])
        results = "\n".join(
            f"- {tc.get('tool_name', '?')}: "
            f"{'error=' + tc['error'] if tc.get('error') else 'ok'}"
            for tc in tool_calls
        ) or "(no actions executed)"
        prompt = VERIFY_PROMPT.format(
            diagnosis=diagnosis,
            tool_call_count=len(tool_calls),
            tool_call_results=results,
        )
        verification = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )
        return {
            "verification": verification,
        }

    return verify


def verify(state: IncidentState) -> dict:
    """Stub verify node (no LLM).  Use ``_build_verify`` for real logic."""
    tool_calls = state.get("tool_calls", [])
    return {
        "verification": f"[verify] Checked {len(tool_calls)} tool call(s)",
    }


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def _build_summarize(llm: LLM, agent_roles: list[str] | None = None):
    """Return a summarize node function closed over the LLM instance."""

    agent_roles = validate_agent_roles(agent_roles)

    def summarize(state: IncidentState) -> dict:
        """Produce a final incident summary."""
        prompt = SUMMARIZE_PROMPT.format(
            incident_description=state.get("incident_description", ""),
            diagnosis=state.get("diagnosis", ""),
            verification=state.get("verification", ""),
            tool_call_count=len(state.get("tool_calls", [])),
            blocked_count=len(state.get("blocked_actions", [])),
        )
        summary = _invoke_multi_agent(
            llm,
            task_prompt=prompt,
            agent_roles=agent_roles,
        )
        status = state.get("status")
        return {
            "summary": summary,
            "status": status if status in {"failed", "timed_out"} else "completed",
        }

    return summarize


def summarize(state: IncidentState) -> dict:
    """Stub summarize node (no LLM).  Use ``_build_summarize`` for real logic."""
    diagnosis = state.get("diagnosis", "")
    verification = state.get("verification", "")
    status = state.get("status")
    return {
        "summary": f"[summarize] Incident session complete. "
                   f"Diagnosis: {diagnosis} | Verification: {verification}",
        "status": status if status in {"failed", "timed_out"} else "completed",
    }


# ---------------------------------------------------------------------------
# remember (Sprint 45 — Step 4)
# ---------------------------------------------------------------------------


def _build_remember(
    llm: LLM,
    memory_factory: Any,
    *,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
    source_incident_id: uuid.UUID | None,
):
    """Return a remember node closed over the LLM + memory binding.

    The closure runs :func:`backend.memory.writeback.remember_for_session`
    which checks `should_remember`, calls the LLM once, parses the JSON
    response, persists a row, and (if the per-service threshold is reached)
    triggers one bounded auto-compaction pass.

    Memory writeback never raises into the workflow. If anything fails the
    node returns an empty delta and the session is unaffected.
    """

    from backend.memory.writeback import remember_for_session

    async def remember(state: IncidentState) -> dict:
        memorized = await remember_for_session(
            memory_factory,
            llm=llm,
            org_id=org_id,
            service_id=service_id,
            source_incident_id=source_incident_id,
            state=dict(state),
        )
        if memorized is None:
            return {}
        return {"memorized_id": str(memorized)}

    return remember


def remember(state: IncidentState) -> dict:
    """Stub remember node (no LLM / no memory). Use ``_build_remember``."""
    return {}
