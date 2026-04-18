"""Tier 0 hard time limits for the LangGraph workflow.

Sprint 17 (time-limits pillar).

Two gates:

* **Per-node wall clock** — each node is wrapped with ``asyncio.wait_for``.
  If the node exceeds ``max_node_seconds`` the wrapper returns a
  structured timeout verdict (partial state update:
  ``status="timed_out"``, ``error=...``).  The graph still advances
  linearly — downstream nodes are free to short-circuit on the state
  flag — but the offending node cannot stall the whole session on its
  own.
* **Session wall clock** — the caller wraps the entire ``graph.ainvoke``
  in ``ainvoke_with_session_timeout``.  On timeout the helper returns a
  best-effort state dict carrying ``status="timed_out"``.

Both limits apply only at Tier 0.  Other tiers leave the graph
untouched (this module is a no-op unless ``build_graph(tier=0, ...)``
explicitly wires it in).
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from typing import Any, Awaitable, Callable


@dataclasses.dataclass(frozen=True)
class Tier0TimeConfig:
    """Hard time limits applied to a Tier 0 session."""

    max_session_seconds: int = 600
    max_node_seconds: int = 120

    def __post_init__(self) -> None:
        if self.max_session_seconds <= 0:
            raise ValueError("max_session_seconds must be positive")
        if self.max_node_seconds <= 0:
            raise ValueError("max_node_seconds must be positive")
        if self.max_node_seconds > self.max_session_seconds:
            # Not strictly illegal, but almost certainly a config bug.
            raise ValueError(
                "max_node_seconds cannot exceed max_session_seconds"
            )


def _timeout_state(node_name: str, seconds: int) -> dict[str, Any]:
    return {
        "status": "timed_out",
        "error": (
            f"Node '{node_name}' exceeded Tier 0 time limit of {seconds}s"
        ),
    }


def wrap_node_with_timeout(
    fn: Callable[..., Any],
    *,
    seconds: int,
    node_name: str,
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    """Return an async node wrapper enforcing a per-node time limit.

    The returned callable is always async — LangGraph handles sync and
    async nodes transparently, so upgrading the signature to async is
    safe.  The wrapped function may be sync or async.

    Sync functions are offloaded to the default executor so the timeout
    is observed at the event-loop level.  The underlying thread is not
    forcibly terminated on timeout — Python offers no portable way — but
    the timeout state update still propagates and the graph advances.
    """

    async def _run(state: Any) -> dict[str, Any]:
        try:
            if inspect.iscoroutinefunction(fn):
                return await asyncio.wait_for(fn(state), timeout=seconds)
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, fn, state),
                timeout=seconds,
            )
        except asyncio.TimeoutError:
            return _timeout_state(node_name, seconds)

    _run.__wrapped__ = fn  # type: ignore[attr-defined]
    _run.__name__ = f"timeout_wrapped_{node_name}"
    return _run


async def ainvoke_with_session_timeout(
    graph: Any,
    initial_state: dict[str, Any],
    *,
    seconds: int,
) -> dict[str, Any]:
    """Run ``graph.ainvoke`` under a hard session wall clock.

    On timeout, returns a shallow-merged state dict: the caller's
    ``initial_state`` with ``status="timed_out"`` + ``error=...``
    overlaid.  The graph coroutine is cancelled via ``wait_for``.
    """
    try:
        return await asyncio.wait_for(
            graph.ainvoke(initial_state), timeout=seconds
        )
    except asyncio.TimeoutError:
        result = dict(initial_state)
        result["status"] = "timed_out"
        result["error"] = (
            f"Session exceeded Tier 0 wall-clock limit of {seconds}s"
        )
        return result
