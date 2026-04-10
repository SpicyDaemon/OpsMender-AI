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

from typing import Any

from backend.agent.llm import LLM
from backend.agent.state import IncidentState
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


# ---------------------------------------------------------------------------
# observe
# ---------------------------------------------------------------------------

def _build_observe(llm: LLM):
    """Return an observe node function closed over the LLM instance."""

    def observe(state: IncidentState) -> dict:
        """Gather initial observations about the incident.

        Sends the incident description to the LLM for structured
        analysis and initial observation gathering.
        """
        description = state.get("incident_description", "")
        prompt = OBSERVE_PROMPT.format(incident_description=description)
        observations = llm.invoke(prompt)
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

def _build_diagnose(llm: LLM):
    """Return a diagnose node function closed over the LLM instance."""

    def diagnose(state: IncidentState) -> dict:
        """Analyse observations and produce a diagnosis."""
        observations = state.get("observations", "")
        prompt = DIAGNOSE_PROMPT.format(observations=observations)
        diagnosis = llm.invoke(prompt)
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

def _build_plan(llm: LLM, tier: int, skill_def: SkillDefinition):
    """Return a plan node function closed over the LLM, tier, and skill def."""

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
            tier=tier,
        )
        raw = llm.invoke(prompt)

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


def plan(state: IncidentState) -> dict:
    """Stub plan node (no LLM).  Use ``_build_plan`` for real logic."""
    return {
        "plan": [],
    }


# ---------------------------------------------------------------------------
# tier_gate  (HARD PROGRAMMATIC CHECK — not an LLM decision)
# ---------------------------------------------------------------------------

def _build_tier_gate(tier: int, skill_def: SkillDefinition):
    """Return a tier_gate node function closed over *tier* and *skill_def*.

    The tier gate is deterministic: it reads the proposed actions from
    ``state["plan"]``, classifies each via the skill definition, and
    splits them into ``approved_actions`` and ``blocked_actions``.

    This function is **never** delegated to an LLM.
    """

    def tier_gate(state: IncidentState) -> dict:
        proposed = state.get("plan", [])
        approved: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for action in proposed:
            tool_name = action.get("tool_name", "")
            enforcement = tier_check(tool_name, tier, skill_def)
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
        }

    return tier_gate


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

def _build_execute(
    mcp_session,
    skill_def: SkillDefinition,
    audit_logger,
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

def _build_verify(llm: LLM):
    """Return a verify node function closed over the LLM instance."""

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
        verification = llm.invoke(prompt)
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

def _build_summarize(llm: LLM):
    """Return a summarize node function closed over the LLM instance."""

    def summarize(state: IncidentState) -> dict:
        """Produce a final incident summary."""
        prompt = SUMMARIZE_PROMPT.format(
            incident_description=state.get("incident_description", ""),
            diagnosis=state.get("diagnosis", ""),
            verification=state.get("verification", ""),
            tool_call_count=len(state.get("tool_calls", [])),
            blocked_count=len(state.get("blocked_actions", [])),
        )
        summary = llm.invoke(prompt)
        return {
            "summary": summary,
            "status": "completed",
        }

    return summarize


def summarize(state: IncidentState) -> dict:
    """Stub summarize node (no LLM).  Use ``_build_summarize`` for real logic."""
    diagnosis = state.get("diagnosis", "")
    verification = state.get("verification", "")
    return {
        "summary": f"[summarize] Incident session complete. "
                   f"Diagnosis: {diagnosis} | Verification: {verification}",
        "status": "completed",
    }
