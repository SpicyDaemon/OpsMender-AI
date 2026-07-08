"""LangGraph workflow builder for the incident response graph.

Builds the default graph described in PROMPT_CONTEXT.md::

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
    recall,
    remember,
    # Builder versions (with dependencies)
    _build_observe,
    _build_diagnose,
    _build_plan,
    _build_plan_with_tool_names,
    _build_workflow_plan,
    _build_tier_gate,
    _build_execute,
    _build_verify,
    _build_summarize,
    _build_recall,
    _build_remember,
)
from backend.skills.parser import SkillDefinition
from backend.agent.workflow_executor import WorkflowExecutor

DEFAULT_WORKFLOW_NODE_ORDER = [
    "recall",
    "observe",
    "diagnose",
    "plan",
    "tier_gate",
    "execute",
    "verify",
    "summarize",
    "remember",
]


def validate_workflow_node_order(node_order: list[str] | None) -> list[str]:
    if node_order is None:
        return list(DEFAULT_WORKFLOW_NODE_ORDER)
    if not node_order:
        raise ValueError("Workflow node order cannot be empty")

    allowed = set(DEFAULT_WORKFLOW_NODE_ORDER)
    cleaned = [str(node).strip() for node in node_order if str(node).strip()]
    if len(cleaned) != len(node_order):
        raise ValueError("Workflow node order cannot contain blank node names")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Workflow node order cannot contain duplicate nodes")

    invalid = [node for node in cleaned if node not in allowed]
    if invalid:
        raise ValueError(f"Unsupported workflow nodes: {', '.join(invalid)}")

    if "execute" in cleaned:
        if "tier_gate" not in cleaned:
            raise ValueError("Workflow including execute must also include tier_gate")
        if cleaned.index("tier_gate") + 1 != cleaned.index("execute"):
            raise ValueError("tier_gate must appear immediately before execute")

    if "tier_gate" in cleaned and "execute" not in cleaned:
        raise ValueError("tier_gate cannot be used without execute")

    return cleaned


def _make_redirect_router(*, next_node: str):
    """Build the conditional router used on the ``tier_gate`` edge.

    Returns ``"plan"`` when the gate flagged a Tier 1 redirect (loop back to
    re-plan with operator guidance), otherwise ``next_node`` (continue the
    pipeline, normally ``execute``).
    """

    def _route(state) -> str:
        if state.get("redirect_requested"):
            return "plan"
        return next_node

    return _route


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
    plan_tool_descriptions: dict[str, str] | None = None,
    tool_caller=None,
    node_event_publisher=None,
    node_order: list[str] | None = None,
    memory_factory=None,
    org_id=None,
    service_id=None,
    source_incident_id=None,
    workflow_enabled: bool = True,
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
    node_order = validate_workflow_node_order(node_order)

    # -- select node implementations ----------------------------------------
    if llm is not None:
        observe_fn = _build_observe(llm)
        diagnose_fn = _build_diagnose(llm)
        if plan_tool_names is not None:
            plan_fn = _build_plan_with_tool_names(
                llm,
                tier,
                skill_def,
                plan_tool_names,
                tool_descriptions=plan_tool_descriptions,
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

    if (
        workflow_enabled
        and skill_def.workflow
        and mcp_session is not None
        and audit_logger is not None
    ):
        plan_fn = _build_workflow_plan(
            WorkflowExecutor(
                mcp_session=mcp_session,
                skill_def=skill_def,
                audit_logger=audit_logger,
                tier=tier,
                approval_service=approval_service,
                tool_caller=tool_caller,
            )
        )

    if memory_factory is not None and org_id is not None:
        recall_fn = _build_recall(
            memory_factory, org_id=org_id, service_id=service_id
        )
    else:
        recall_fn = recall

    if memory_factory is not None and org_id is not None and llm is not None:
        remember_fn = _build_remember(
            llm,
            memory_factory,
            org_id=org_id,
            service_id=service_id,
            source_incident_id=source_incident_id,
        )
    else:
        remember_fn = remember

    tier_gate_fn = _build_tier_gate(tier, skill_def, approval_service)

    # -- Tier 0 per-node timeouts -------------------------------------------
    # When a Tier 0 time config is supplied, every node is wrapped with a
    # hard wall clock.  This is the sandbox's second safety gate — the
    # agent cannot hang a session on a single slow LLM call.
    if tier == 0 and tier0_time_config is not None:
        secs = tier0_time_config.max_node_seconds
        recall_fn = wrap_node_with_timeout(recall_fn, seconds=secs, node_name="recall")
        observe_fn = wrap_node_with_timeout(observe_fn, seconds=secs, node_name="observe")
        diagnose_fn = wrap_node_with_timeout(diagnose_fn, seconds=secs, node_name="diagnose")
        plan_fn = wrap_node_with_timeout(plan_fn, seconds=secs, node_name="plan")
        tier_gate_fn = wrap_node_with_timeout(tier_gate_fn, seconds=secs, node_name="tier_gate")
        execute_fn = wrap_node_with_timeout(execute_fn, seconds=secs, node_name="execute")
        verify_fn = wrap_node_with_timeout(verify_fn, seconds=secs, node_name="verify")
        summarize_fn = wrap_node_with_timeout(summarize_fn, seconds=secs, node_name="summarize")
        remember_fn = wrap_node_with_timeout(remember_fn, seconds=secs, node_name="remember")

    if node_event_publisher is not None:
        recall_fn = _wrap_node_with_events(
            recall_fn, node_name="recall", publisher=node_event_publisher
        )
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
        remember_fn = _wrap_node_with_events(
            remember_fn, node_name="remember", publisher=node_event_publisher
        )

    # -- register nodes ------------------------------------------------------
    node_impls = {
        "recall": recall_fn,
        "observe": observe_fn,
        "diagnose": diagnose_fn,
        "plan": plan_fn,
        "tier_gate": tier_gate_fn,
        "execute": execute_fn,
        "verify": verify_fn,
        "summarize": summarize_fn,
        "remember": remember_fn,
    }
    for node_name in node_order:
        builder.add_node(node_name, node_impls[node_name])

    # -- wire edges ----------------------------------------------------------
    # Mostly a linear pipeline. The one exception is ``tier_gate``: at Tier 1
    # an operator can "redirect" a proposed action with free-text guidance, in
    # which case the gate sets ``redirect_requested`` and the conditional edge
    # routes back to the ``plan`` node so the agent re-plans with the steering
    # in context. Without a plan node there is nothing to loop back to, so the
    # edge stays linear.
    builder.add_edge(START, node_order[0])
    can_loop = "plan" in node_order
    for current, nxt in zip(node_order, node_order[1:]):
        if current == "tier_gate" and can_loop:
            builder.add_conditional_edges(
                "tier_gate",
                _make_redirect_router(next_node=nxt),
                {"plan": "plan", nxt: nxt},
            )
        else:
            builder.add_edge(current, nxt)
    builder.add_edge(node_order[-1], END)

    return builder.compile()
