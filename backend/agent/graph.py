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

from langgraph.graph import StateGraph, START, END

from backend.agent.llm import LLM
from backend.agent.state import IncidentState
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
    _build_tier_gate,
    _build_execute,
    _build_verify,
    _build_summarize,
)
from backend.skills.parser import SkillDefinition


def build_graph(
    *,
    tier: int,
    skill_def: SkillDefinition,
    llm: LLM | None = None,
    mcp_session=None,
    audit_logger=None,
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
        execute_fn = _build_execute(mcp_session, skill_def, audit_logger)
    else:
        execute_fn = execute

    # -- register nodes ------------------------------------------------------
    builder.add_node("observe", observe_fn)
    builder.add_node("diagnose", diagnose_fn)
    builder.add_node("plan", plan_fn)
    builder.add_node("tier_gate", _build_tier_gate(tier, skill_def))
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
