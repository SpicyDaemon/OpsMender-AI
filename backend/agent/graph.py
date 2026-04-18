"""LangGraph workflow builder for the incident response graph.

Builds the default graph described in REFERENCE.md::

    observe → diagnose → plan → tier_gate → execute → verify → summarize

Usage (stub mode, no LLM / MCP)::

    from backend.agent.graph import build_graph
    from backend.skills.parser import SkillDefinition

    skill_def = SkillDefinition.load("examples/SKILL.md")
    graph = build_graph(tier=2, skill_def=skill_def)
    result = graph.invoke({...})

Usage (full mode, with LLM and MCP)::

    from backend.agent.graph import build_graph
    from backend.agent.llm import StubLLM
    from backend.audit.logger import AuditLogger

    graph = build_graph(
        tier=2, skill_def=skill_def,
        llm=my_llm,
        mcp_session=session,
        audit_logger=AuditLogger("logs/audit.jsonl"),
    )
    result = await graph.ainvoke({...})
"""

from __future__ import annotations

import inspect

from langgraph.graph import StateGraph, START, END

from backend.agent.llm import LLM
from backend.agent.state import IncidentState
from backend.agent.timeouts import Tier0TimeConfig, wrap_node_with_timeout
from backend.approvals import ApprovalService
from backend.agent.nodes import (
    # Stub versions (no LLM / no MCP)
    observe,
    diagnose,
    plan,
    execute,
    verify,
    summarize,
    # Builder versions (with dependencies)
    _build_observe,
    _build_diagnose,
    _build_plan,
    _build_plan_with_tool_names,
    _build_tier_gate,
    _build_execute,
    _build_verify,
    _build_summarize,
)
from backend.skills.parser import SkillDefinition


def _wrap_node_with_events(fn, *, node_name: str, publisher):
    async def _run(state):
        await publisher(node_name, "started", state)
        try:
            result = fn(state)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            await publisher(node_name, "failed", {"error": True})
            raise

        status = "completed"
        if isinstance(result, dict):
            status = result.get("status") or "completed"
        await publisher(node_name, status, result)
        return result

    _run.__wrapped__ = fn  # type: ignore[attr-defined]
    _run.__name__ = f"event_wrapped_{node_name}"
    return _run


def build_graph(
    *,
    tier: int,
    skill_def: SkillDefinition,
    llm: LLM | None = None,
    mcp_session=None,
    audit_logger=None,
    approval_service: ApprovalService | None = None,
    tier0_time_config: Tier0TimeConfig | None = None,
    plan_tool_names: list[str] | None = None,
    tool_caller=None,
    node_event_publisher=None,
):
    """Construct and compile the incident response workflow graph.

    Parameters
    ----------
    tier:
        Active tier (0–3).  Injected into the tier_gate and plan nodes.
    skill_def:
        Loaded skill definition for tool classification.
    llm:
        LLM instance for powering observe/diagnose/plan/verify/summarize.
        If ``None``, stub (pass-through) nodes are used instead.
    mcp_session:
        Active MCP ``ClientSession`` for the execute node.
        If ``None``, the stub execute node is used (no tool calls).
    audit_logger:
        ``AuditLogger`` instance for recording tool calls.
        Required when ``mcp_session`` is provided.

    Returns
    -------
    langgraph.graph.state.CompiledStateGraph
        A compiled graph ready for ``.invoke()`` or ``.ainvoke()``.
    """
    builder = StateGraph(IncidentState)

    # -- select node implementations ----------------------------------------
    if llm is not None:
        observe_fn = _build_observe(llm)
        diagnose_fn = _build_diagnose(llm)
        if plan_tool_names is not None:
            plan_fn = _build_plan_with_tool_names(
                llm, tier, skill_def, plan_tool_names
            )
        else:
            plan_fn = _build_plan(llm, tier, skill_def)
        verify_fn = _build_verify(llm)
        summarize_fn = _build_summarize(llm)
    else:
        observe_fn = observe
        diagnose_fn = diagnose
        plan_fn = plan
        verify_fn = verify
        summarize_fn = summarize

    if mcp_session is not None and audit_logger is not None:
        execute_fn = _build_execute(
            mcp_session,
            skill_def,
            audit_logger,
            tool_caller=tool_caller,
        )
    else:
        execute_fn = execute

    tier_gate_fn = _build_tier_gate(tier, skill_def, approval_service)

    # -- Tier 0 per-node timeouts -------------------------------------------
    # When a Tier 0 time config is supplied, every node is wrapped with a
    # hard wall clock.  This is the sandbox's second safety gate — the
    # agent cannot hang a session on a single slow LLM call.
    if tier == 0 and tier0_time_config is not None:
        secs = tier0_time_config.max_node_seconds
        observe_fn = wrap_node_with_timeout(observe_fn, seconds=secs, node_name="observe")
        diagnose_fn = wrap_node_with_timeout(diagnose_fn, seconds=secs, node_name="diagnose")
        plan_fn = wrap_node_with_timeout(plan_fn, seconds=secs, node_name="plan")
        tier_gate_fn = wrap_node_with_timeout(tier_gate_fn, seconds=secs, node_name="tier_gate")
        execute_fn = wrap_node_with_timeout(execute_fn, seconds=secs, node_name="execute")
        verify_fn = wrap_node_with_timeout(verify_fn, seconds=secs, node_name="verify")
        summarize_fn = wrap_node_with_timeout(summarize_fn, seconds=secs, node_name="summarize")

    if node_event_publisher is not None:
        observe_fn = _wrap_node_with_events(
            observe_fn, node_name="observe", publisher=node_event_publisher
        )
        diagnose_fn = _wrap_node_with_events(
            diagnose_fn, node_name="diagnose", publisher=node_event_publisher
        )
        plan_fn = _wrap_node_with_events(
            plan_fn, node_name="plan", publisher=node_event_publisher
        )
        tier_gate_fn = _wrap_node_with_events(
            tier_gate_fn, node_name="tier_gate", publisher=node_event_publisher
        )
        execute_fn = _wrap_node_with_events(
            execute_fn, node_name="execute", publisher=node_event_publisher
        )
        verify_fn = _wrap_node_with_events(
            verify_fn, node_name="verify", publisher=node_event_publisher
        )
        summarize_fn = _wrap_node_with_events(
            summarize_fn, node_name="summarize", publisher=node_event_publisher
        )

    # -- register nodes ------------------------------------------------------
    builder.add_node("observe", observe_fn)
    builder.add_node("diagnose", diagnose_fn)
    builder.add_node("plan", plan_fn)
    builder.add_node("tier_gate", tier_gate_fn)
    builder.add_node("execute", execute_fn)
    builder.add_node("verify", verify_fn)
    builder.add_node("summarize", summarize_fn)

    # -- wire edges (linear pipeline) ----------------------------------------
    builder.add_edge(START, "observe")
    builder.add_edge("observe", "diagnose")
    builder.add_edge("diagnose", "plan")
    builder.add_edge("plan", "tier_gate")
    builder.add_edge("tier_gate", "execute")
    builder.add_edge("execute", "verify")
    builder.add_edge("verify", "summarize")
    builder.add_edge("summarize", END)

    return builder.compile()
