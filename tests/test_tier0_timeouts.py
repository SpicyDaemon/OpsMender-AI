"""Tests for Tier 0 hard time limits (backend.agent.timeouts)."""

from __future__ import annotations

import asyncio

import pytest

from backend.agent.timeouts import (
    Tier0TimeConfig,
    ainvoke_with_session_timeout,
    wrap_node_with_timeout,
)


class TestTier0TimeConfig:
    def test_defaults(self):
        cfg = Tier0TimeConfig()
        assert cfg.max_session_seconds == 600
        assert cfg.max_node_seconds == 120

    def test_non_positive_session_seconds_rejected(self):
        with pytest.raises(ValueError, match="max_session_seconds"):
            Tier0TimeConfig(max_session_seconds=0, max_node_seconds=30)

    def test_non_positive_node_seconds_rejected(self):
        with pytest.raises(ValueError, match="max_node_seconds"):
            Tier0TimeConfig(max_session_seconds=60, max_node_seconds=-1)

    def test_node_cannot_exceed_session(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            Tier0TimeConfig(max_session_seconds=10, max_node_seconds=30)


class TestWrapNodeWithTimeout:
    async def test_async_node_within_limit_passes(self):
        async def fast_node(state):
            await asyncio.sleep(0)
            return {"observations": "ok", **state}

        wrapped = wrap_node_with_timeout(fast_node, seconds=5, node_name="observe")
        result = await wrapped({"session_id": "abc"})
        assert result["observations"] == "ok"
        assert "status" not in result or result.get("status") != "timed_out"

    async def test_async_node_timeout_returns_structured_verdict(self):
        async def slow_node(state):
            await asyncio.sleep(1.0)
            return {"observations": "nope"}

        wrapped = wrap_node_with_timeout(slow_node, seconds=0.05, node_name="observe")
        result = await wrapped({})
        assert result["status"] == "timed_out"
        assert "observe" in result["error"]
        assert "0.05" in result["error"]

    async def test_sync_node_within_limit_passes(self):
        def fast_node(state):
            return {"diagnosis": "root cause"}

        wrapped = wrap_node_with_timeout(fast_node, seconds=5, node_name="diagnose")
        result = await wrapped({})
        assert result["diagnosis"] == "root cause"

    async def test_sync_node_timeout_returns_structured_verdict(self):
        import time

        def slow_node(state):
            time.sleep(1.0)
            return {"diagnosis": "nope"}

        wrapped = wrap_node_with_timeout(slow_node, seconds=0.05, node_name="diagnose")
        result = await wrapped({})
        assert result["status"] == "timed_out"
        assert "diagnose" in result["error"]


class _FakeGraph:
    """Minimal stand-in for CompiledStateGraph.ainvoke()."""

    def __init__(self, delay: float, outcome: dict):
        self._delay = delay
        self._outcome = outcome

    async def ainvoke(self, state):
        await asyncio.sleep(self._delay)
        return {**state, **self._outcome}


class TestAinvokeWithSessionTimeout:
    async def test_returns_graph_result_within_limit(self):
        graph = _FakeGraph(delay=0.01, outcome={"summary": "done"})
        result = await ainvoke_with_session_timeout(
            graph, {"session_id": "abc", "tier": 0}, seconds=1
        )
        assert result["summary"] == "done"
        assert result["session_id"] == "abc"

    async def test_session_timeout_returns_timed_out_state(self):
        graph = _FakeGraph(delay=1.0, outcome={"summary": "never"})
        result = await ainvoke_with_session_timeout(
            graph,
            {"session_id": "abc", "tier": 0},
            seconds=0.05,
        )
        assert result["status"] == "timed_out"
        assert "Session exceeded" in result["error"]
        assert "0.05" in result["error"]
        # Initial state preserved
        assert result["session_id"] == "abc"
        assert result["tier"] == 0


class TestConfigLoaderWiresTier0(object):
    """The Tier0Config env vars are exposed on AppConfig."""

    def test_defaults(self, tmp_path, monkeypatch):
        from backend.config_loader import AppConfig

        env_file = tmp_path / ".env"
        env_file.write_text("")
        # Clear any env pollution
        for k in (
            "OPSMENDER_TIER0_MAX_SESSION_SECONDS",
            "OPSMENDER_TIER0_MAX_NODE_SECONDS",
        ):
            monkeypatch.delenv(k, raising=False)
        cfg = AppConfig.load(env_file)
        assert cfg.tier0.max_session_seconds == 600
        assert cfg.tier0.max_node_seconds == 120

    def test_override_from_env(self, tmp_path, monkeypatch):
        from backend.config_loader import AppConfig

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPSMENDER_TIER0_MAX_SESSION_SECONDS=300\nOPSMENDER_TIER0_MAX_NODE_SECONDS=45\n"
        )
        for k in (
            "OPSMENDER_TIER0_MAX_SESSION_SECONDS",
            "OPSMENDER_TIER0_MAX_NODE_SECONDS",
        ):
            monkeypatch.delenv(k, raising=False)
        cfg = AppConfig.load(env_file)
        assert cfg.tier0.max_session_seconds == 300
        assert cfg.tier0.max_node_seconds == 45
