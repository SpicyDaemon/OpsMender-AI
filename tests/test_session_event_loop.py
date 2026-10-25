"""Model calls in a session never hold the event loop (M1-87).

The 2026-10-24 live round found that while a Tier 1 or Tier 2 session waited
on a slow local model, the whole API stopped answering: the node event wrapper
called each synchronous model node directly on the event loop, and the memory
writeback called the model inside async functions. These tests run a model
that blocks its thread for a fixed time and keep a ticker on the event loop;
the loop must never pause for as long as one model call, and the model must
run on another thread.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from backend.agent.graph import build_graph
from backend.agent.timeouts import Tier0TimeConfig
from backend.memory.writeback import _llm_compact, remember_for_session
from backend.skills.parser import SkillDefinition
from tests.skill_policy_helpers import explicit_operation

MODEL_SECONDS = 0.4
# The loop may pause briefly for scheduling, never for a model call.
MAX_LOOP_PAUSE = MODEL_SECONDS / 2


class _BlockingLLM:
    """A model whose every call blocks its thread, like a slow local model."""

    def __init__(self, response: str = "[]") -> None:
        self.response = response
        self.calls = 0
        self.threads: set[int] = set()

    def invoke(self, prompt: str) -> str:
        self.calls += 1
        self.threads.add(threading.get_ident())
        time.sleep(MODEL_SECONDS)
        return self.response


async def _with_ticker(coro):
    """Await *coro* while a ticker measures the longest event-loop pause."""
    loop = asyncio.get_running_loop()
    pauses: list[float] = []
    stop = asyncio.Event()

    async def tick() -> None:
        last = loop.time()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = loop.time()
            pauses.append(now - last)
            last = now

    ticker = asyncio.create_task(tick())
    try:
        result = await coro
    finally:
        stop.set()
        await ticker
    return result, max(pauses)


def _skill_def() -> SkillDefinition:
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[explicit_operation("get_pods", "safe")],
    )


@pytest.mark.parametrize("tier", [0, 1, 2])
async def test_session_graph_with_event_publisher_keeps_the_loop_free(tier):
    """Sessions always build the graph with a node event publisher; the model
    nodes must run off the event loop at every tier, not only at Tier 0."""
    llm = _BlockingLLM()
    events: list[tuple[str, str]] = []

    async def publisher(node_name, status, _payload=None):
        events.append((node_name, status))

    graph = build_graph(
        tier=tier,
        skill_def=_skill_def(),
        llm=llm,
        node_event_publisher=publisher,
        tier0_time_config=Tier0TimeConfig(max_session_seconds=60, max_node_seconds=30)
        if tier == 0
        else None,
    )
    loop_thread = threading.get_ident()

    result, pause = await _with_ticker(
        graph.ainvoke(
            {
                "session_id": "m1-87",
                "tier": tier,
                "incident_description": "productpage returns 500s",
            }
        )
    )

    assert result["status"] == "completed"
    assert llm.calls >= 4  # observe, diagnose, plan and summarize at least
    assert loop_thread not in llm.threads
    assert pause < MAX_LOOP_PAUSE, f"event loop paused {pause:.2f}s during a model call"
    assert ("summarize", "completed") in events


async def test_memory_writeback_calls_the_model_off_the_loop():
    llm = _BlockingLLM(response="not a memory")
    state = {
        "status": "completed",
        "summary": "The productpage returned 500s; the cause is unknown.",
    }
    loop_thread = threading.get_ident()

    memory_id, pause = await _with_ticker(
        remember_for_session(
            object(),  # never opened: the unparseable answer ends the writeback first
            llm=llm,
            org_id=uuid.uuid4(),
            service_id=None,
            source_incident_id=None,
            state=state,
        )
    )

    assert memory_id is None
    assert llm.calls == 1
    assert loop_thread not in llm.threads
    assert pause < MAX_LOOP_PAUSE, (
        f"event loop paused {pause:.2f}s during the writeback's model call"
    )


async def test_memory_compaction_calls_the_model_off_the_loop():
    llm = _BlockingLLM(response="[]")
    memory = SimpleNamespace(
        id=uuid.uuid4(),
        title="ratings OOM",
        tags=["oom"],
        summary_md="ratings ran out of memory",
    )
    loop_thread = threading.get_ident()

    deleted, pause = await _with_ticker(
        _llm_compact(None, llm=llm, memories=[memory], org_id=uuid.uuid4())
    )

    assert deleted == 0
    assert llm.calls == 1
    assert loop_thread not in llm.threads
    assert pause < MAX_LOOP_PAUSE, (
        f"event loop paused {pause:.2f}s during the compaction's model call"
    )
