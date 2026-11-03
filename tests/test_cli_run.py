"""Tests for the ``opsmender run`` CLI command."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.llm import StubLLM
from backend.db.bootstrap import initialize_sqlite_schema
from backend.db.models import ApprovalRequest, Organization, Session
from backend.mcp.client import tool_plan_description
from cli.opsmender import main, _parse_args


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestRunArgParsing:
    def test_run_requires_incident(self):
        with pytest.raises(SystemExit):
            _parse_args(["run"])

    def test_run_with_incident(self):
        args = _parse_args(["run", "--incident", "pods crashing"])
        assert args.command == "run"
        assert args.incident == "pods crashing"
        assert args.tier is None
        assert args.dry_run is False
        assert args.skill_file == "examples/SKILL.md"
        assert args.output is None

    def test_run_with_all_options(self):
        args = _parse_args(
            [
                "run",
                "--incident",
                "high latency",
                "--tier",
                "3",
                "--skill-file",
                "my/SKILL.md",
                "--model",
                "claude-haiku-4-5-20251001",
                "--dry-run",
                "--output",
                "out.json",
            ]
        )
        assert args.tier == 3
        assert args.skill_file == "my/SKILL.md"
        assert args.model == "claude-haiku-4-5-20251001"
        assert args.dry_run is True
        assert args.output == "out.json"


# ---------------------------------------------------------------------------
# Dry-run end-to-end (stub LLM, no MCP, no API key needed)
# ---------------------------------------------------------------------------


class TestRunDryRun:
    """End-to-end tests using --dry-run (stub LLM, no MCP)."""

    def test_dry_run_succeeds(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test incident",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "INCIDENT RESPONSE COMPLETE" in out
        assert "Session:" in out
        assert "Dry-run mode" in out

    def test_dry_run_with_tier_override(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "pod OOMKilled",
                    "--tier",
                    "3",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Tier:     3" in out

    def test_dry_run_writes_output_file(self, tmp_path, capsys):
        out_file = tmp_path / "result.json"
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "disk full",
                    "--dry-run",
                    "--output",
                    str(out_file),
                ]
            )
        assert exc_info.value.code == 0
        assert out_file.is_file()
        data = json.loads(out_file.read_text())
        assert data["status"] == "completed"
        assert data["incident_description"] == "disk full"
        assert "session_id" in data

    def test_dry_run_creates_audit_entries(self, tmp_path, capsys):
        cfg_file = tmp_path / ".env"
        audit_file = tmp_path / "audit.jsonl"
        cfg_file.write_text(
            f"OPSMENDER_TIER=2\nOPSMENDER_LOG_LEVEL=INFO\nOPSMENDER_AUDIT_LOG={audit_file}\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    str(cfg_file),
                    "run",
                    "--incident",
                    "test",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        assert audit_file.is_file()
        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) >= 2  # at least session_start + session_end
        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert first["entry_type"] == "session_start"
        assert last["entry_type"] == "session_end"

    def test_invalid_tier_fails(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test",
                    "--tier",
                    "5",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Invalid tier" in err

    def test_missing_skill_file_fails(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test",
                    "--skill-file",
                    "/tmp/nonexistent_skill.md",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Skill file not found" in err


# ---------------------------------------------------------------------------
# AnthropicLLM unit tests (no API calls)
# ---------------------------------------------------------------------------


class TestAnthropicLLM:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from backend.agent.llm import AnthropicLLM

        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            AnthropicLLM()

    def test_missing_package_raises(self, monkeypatch):
        """Simulate anthropic not being installed."""
        import sys

        # Save and remove anthropic from sys.modules temporarily
        saved = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # type: ignore[assignment]
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        try:
            from backend.agent.llm import AnthropicLLM

            with pytest.raises((ImportError, EnvironmentError)):
                AnthropicLLM()
        finally:
            if saved is not None:
                sys.modules["anthropic"] = saved
            else:
                sys.modules.pop("anthropic", None)


# ---------------------------------------------------------------------------
# M1-105: Tier 1 with a database, and the server's tools at Tier 2
# ---------------------------------------------------------------------------


def _workspace_config(tmp_path):
    """A SQLite database with the schema and one organization, and a config
    file pointing the CLI at it. Returns the config path, the URL and the
    organization id."""
    db_path = tmp_path / "cli.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    async def prepare():
        engine = create_async_engine(url)
        await initialize_sqlite_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        org_id = uuid.uuid4()
        async with factory() as db:
            db.add(Organization(id=org_id, name="CLI test", slug="cli-test"))
            await db.commit()
        await engine.dispose()
        return org_id

    org_id = asyncio.run(prepare())
    cfg = tmp_path / ".env"
    cfg.write_text(
        f"OPSMENDER_DATABASE_URL={url}\n"
        f"OPSMENDER_AUDIT_LOG={(tmp_path / 'audit.jsonl').as_posix()}\n"
    )
    return cfg, url, org_id


def _sessions(url):
    async def rows():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            sessions = (await db.execute(select(Session))).scalars().all()
        await engine.dispose()
        return sessions

    return asyncio.run(rows())


class TestRunWithWorkspace:
    def test_tier_1_starts_a_session_in_the_workspace(self, tmp_path, capsys):
        """Before, the session and the approval service were created without
        the workspace id, so Tier 1 stopped with "requires a reachable
        database" even when the database answered."""
        cfg, url, org_id = _workspace_config(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    str(cfg),
                    "run",
                    "--incident",
                    "pods crashing",
                    "--tier",
                    "1",
                    "--dry-run",
                ]
            )

        out, err = capsys.readouterr()
        assert exc_info.value.code == 0, err
        assert "requires a reachable database" not in err
        [session] = _sessions(url)
        assert session.org_id == org_id and session.tier == 1
        # The run closes its row: before, the update left out the workspace
        # id and the row stayed active for good.
        assert session.status == "completed"
        assert session.ended_at is not None
        assert f"Session:  {session.id}" in out
        assert "INCIDENT RESPONSE COMPLETE" in out

    def test_tier_2_plans_with_the_servers_tools(self, tmp_path, capsys, monkeypatch):
        """Before, no tool list reached the graph at Tiers 2 and 3, so the
        plan listed the skill's entries instead of the server's tools."""
        cfg, url, org_id = _workspace_config(tmp_path)
        server = SimpleNamespace(name="lab", transport="http")
        seen: dict = {}

        class FakePool:
            def __init__(self, session_factory, env_fallback=None):
                pass

            async def list_servers(self, org_id=None, *, active_only=True):
                seen["pool_org_id"] = org_id
                return [server]

            async def get_server(self, org_id=None, name=None):
                seen["pool_org_id"] = org_id
                return server

        @contextlib.asynccontextmanager
        async def fake_connect(config):
            assert config is server
            yield object()

        async def fake_list_tools(session):
            seen["tools"] = [
                SimpleNamespace(
                    name="restart_pod",
                    description="Restart one pod",
                    inputSchema={"properties": {"pod": {"type": "string"}}},
                ),
                SimpleNamespace(
                    name="get_pods",
                    description="List pods",
                    inputSchema={"properties": {"namespace": {"type": "string"}}},
                ),
            ]
            return seen["tools"]

        class FakeGraph:
            async def ainvoke(self, state, *args, **kwargs):
                return {"status": "completed", "summary": "ok"}

        def fake_build_graph(**kwargs):
            seen["graph"] = kwargs
            return FakeGraph()

        import backend.agent.graph as graph_module
        import backend.agent.llm as llm_module
        import cli.opsmender as cli_module

        monkeypatch.setattr(cli_module, "MCPServerPool", FakePool)
        monkeypatch.setattr(cli_module, "connect", fake_connect)
        monkeypatch.setattr(cli_module, "list_tools", fake_list_tools)
        monkeypatch.setattr(graph_module, "build_graph", fake_build_graph)
        monkeypatch.setattr(llm_module, "AnthropicLLM", lambda **kw: StubLLM())

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    str(cfg),
                    "run",
                    "--incident",
                    "pods crashing",
                    "--tier",
                    "2",
                ]
            )

        out, err = capsys.readouterr()
        assert exc_info.value.code == 0, err
        # The server was looked up under the workspace, not the fallback.
        assert seen["pool_org_id"] == org_id
        graph = seen["graph"]
        assert graph["tier"] == 2
        assert graph["plan_tool_names"] == ["get_pods", "restart_pod"]
        # The plan sees each tool's arguments, as the session runner shows them.
        assert graph["plan_tool_descriptions"] == {
            tool.name: tool_plan_description(tool) for tool in seen["tools"]
        }
        assert "namespace" in graph["plan_tool_descriptions"]["get_pods"]
        assert "List pods" in graph["plan_tool_descriptions"]["get_pods"]
        assert "2 tool(s) offered to the plan from lab." in out

    def test_tier_2_keeps_the_env_servers_when_the_workspace_has_none(
        self, tmp_path, capsys, monkeypatch
    ):
        """The pool answers from the database under a workspace; a workspace
        with no registered server must still fall back to the .env servers,
        as the command did before it knew the workspace."""
        cfg, url, org_id = _workspace_config(tmp_path)
        env_server = SimpleNamespace(name="env-lab", transport="stdio")
        asked: list = []

        class FakePool:
            def __init__(self, session_factory, env_fallback=None):
                pass

            async def list_servers(self, org_id=None, *, active_only=True):
                asked.append(org_id)
                return [] if org_id is not None else [env_server]

            async def get_server(self, org_id=None, name=None):
                raise AssertionError("no server was named")

        @contextlib.asynccontextmanager
        async def fake_connect(config):
            assert config is env_server
            yield object()

        async def fake_list_tools(session):
            return [SimpleNamespace(name="get_pods", description="", inputSchema={})]

        class FakeGraph:
            async def ainvoke(self, state, *args, **kwargs):
                return {"status": "completed", "summary": "ok"}

        import backend.agent.graph as graph_module
        import backend.agent.llm as llm_module
        import cli.opsmender as cli_module

        monkeypatch.setattr(cli_module, "MCPServerPool", FakePool)
        monkeypatch.setattr(cli_module, "connect", fake_connect)
        monkeypatch.setattr(cli_module, "list_tools", fake_list_tools)
        monkeypatch.setattr(graph_module, "build_graph", lambda **kw: FakeGraph())
        monkeypatch.setattr(llm_module, "AnthropicLLM", lambda **kw: StubLLM())

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "run", "--incident", "x", "--tier", "2"])

        out, err = capsys.readouterr()
        assert exc_info.value.code == 0, err
        assert asked == [org_id, None]
        assert "Connecting to MCP server: env-lab" in out

    def test_tier_1_refuses_a_database_without_a_workspace(self, tmp_path, capsys):
        """A fresh SQLite database has the schema but no organization until
        the first `opsmender serve`; Tier 1 refuses instead of writing the
        session and its approvals under a placeholder workspace id."""
        db_path = tmp_path / "bare.db"
        url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

        async def prepare():
            engine = create_async_engine(url)
            await initialize_sqlite_schema(engine)
            await engine.dispose()

        asyncio.run(prepare())
        cfg = tmp_path / ".env"
        cfg.write_text(
            f"OPSMENDER_DATABASE_URL={url}\n"
            f"OPSMENDER_AUDIT_LOG={(tmp_path / 'audit.jsonl').as_posix()}\n"
        )

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    str(cfg),
                    "run",
                    "--incident",
                    "x",
                    "--tier",
                    "1",
                    "--dry-run",
                ]
            )

        err = capsys.readouterr().err
        assert exc_info.value.code == 1
        assert "requires a reachable database with a workspace" in err
        assert _sessions(url) == []


def _mark_stopped(url, session_id, *, expire_approvals=True):
    """What the dashboard's Stop does to the row and its approvals."""

    async def mark():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                Session.__table__.update()
                .where(Session.id == uuid.UUID(session_id))
                .values(status="stopped")
            )
            if expire_approvals:
                await conn.execute(
                    ApprovalRequest.__table__.update()
                    .where(ApprovalRequest.session_id == uuid.UUID(session_id))
                    .where(ApprovalRequest.status == "pending")
                    .values(status="expired")
                )
        await engine.dispose()

    return mark()


def _approvals(url):
    async def rows():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            found = (await db.execute(select(ApprovalRequest))).scalars().all()
        await engine.dispose()
        return found

    return asyncio.run(rows())


SKILL_T1 = """---
version: "1"
environment: t
operations:
  - tool: get_pods
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: autonomous}
  - tool: restart_pod
    classification: caution
    reversible: true
    compensating_inverse: restart_pod
    tiers:
      T0: {enabled: true, mode: autonomous, require_reversible: true}
      T1: {enabled: true, mode: approval}
      T2: {enabled: false, mode: blocked}
---
"""
PLAN_MARK = "planning remediation"
SUMMARY_MARK = "writing an incident summary"
DIAGNOSE_MARK = "diagnosing an incident"
NOT_ASKED = "the session was stopped; the approval was not asked"
# A read, an approval step and the same read again, all at Tier 1.
WORKFLOW_T1 = """## Workflow
```yaml
steps:
  - id: look
    description: List the pods
    tool: get_pods
    inputs:
      namespace: shop
  - id: fix
    description: Restart the pod
    tool: restart_pod
    inputs:
      pod: ratings-1
  - id: look_again
    description: List the pods again
    tool: get_pods
    inputs:
      namespace: shop
```
"""


def _db_path(url):
    return url.split("///", 1)[1]


def _stop_in_db(db_path, *, expire=True):
    """The dashboard's Stop, written from outside the CLI's event loop."""
    con = sqlite3.connect(db_path, timeout=10)
    try:
        con.execute("UPDATE sessions SET status = 'stopped'")
        if expire:
            con.execute(
                "UPDATE approval_requests SET status = 'expired' WHERE status = 'pending'"
            )
        con.commit()
    finally:
        con.close()


def _override_in_db(db_path):
    """The dashboard's Override to Tier 2: the pending approvals expire and the
    row goes back to active at Tier 2 for the app to run."""
    con = sqlite3.connect(db_path, timeout=10)
    try:
        con.execute(
            "UPDATE approval_requests SET status = 'expired' WHERE status = 'pending'"
        )
        con.execute("UPDATE sessions SET status = 'active', tier = 2")
        con.commit()
    finally:
        con.close()


def _pending_count(db_path):
    con = sqlite3.connect(db_path, timeout=10)
    try:
        return con.execute(
            "SELECT count(*) FROM approval_requests WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        con.close()


def _stop_once_asked(db_path, act=None):
    def watch():
        for _ in range(400):
            try:
                pending = _pending_count(db_path)
                if pending:
                    (act or _stop_in_db)(db_path)
                    return
            except sqlite3.OperationalError:
                pass
            time.sleep(0.05)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def _audit(tmp_path):
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTier1StopWithTheRealGraph:
    """M1-105 fix review: the stop is honoured inside LangGraph nodes, so
    these run the real compiled graph with only the model, the MCP connection
    and the tool call faked."""

    def _run(
        self,
        tmp_path,
        monkeypatch,
        plan,
        on_prompt=None,
        extra=(),
        poll=60.0,
        on_call=None,
        respond=None,
        skill_text=SKILL_T1,
        on_connect=None,
        expect=SystemExit,
    ):
        cfg, url, _org_id = _workspace_config(tmp_path)
        skill = tmp_path / "SKILL.md"
        skill.write_text(skill_text)
        calls: list[str] = []
        prompts: list[str] = []
        self.prompts = prompts
        server = SimpleNamespace(name="lab", transport="http")

        class FakePool:
            def __init__(self, session_factory, env_fallback=None):
                pass

            async def list_servers(self, org_id=None, *, active_only=True):
                return [server]

            async def get_server(self, org_id=None, name=None):
                return server if name == "lab" else None

        @contextlib.asynccontextmanager
        async def fake_connect(config):
            if on_connect is not None:
                on_connect(_db_path(url))
            yield object()

        async def fake_list_tools(session):
            return [
                SimpleNamespace(
                    name="get_pods", description="List pods", inputSchema={}
                ),
                SimpleNamespace(
                    name="restart_pod", description="Restart one pod", inputSchema={}
                ),
            ]

        async def fake_call(session, tool_name, params):
            calls.append(tool_name)
            if on_call is not None:
                await on_call(tool_name, _db_path(url))
            if respond is not None:
                return respond(tool_name, params)
            return CallToolResult(
                isError=False, content=[TextContent(type="text", text="ok")]
            )

        class PlanLLM(StubLLM):
            def invoke(self, prompt):
                prompts.append(prompt)
                if on_prompt is not None:
                    on_prompt(prompt, _db_path(url))
                return json.dumps(plan) if PLAN_MARK in prompt else "[stub]"

        import backend.agent.llm as llm_module
        import cli.opsmender as cli_module

        monkeypatch.setattr(cli_module, "MCPServerPool", FakePool)
        monkeypatch.setattr(cli_module, "connect", fake_connect)
        monkeypatch.setattr(cli_module, "list_tools", fake_list_tools)
        monkeypatch.setattr(cli_module, "mcp_call_tool", fake_call)
        monkeypatch.setattr(cli_module, "STOP_POLL_SECONDS", poll)
        monkeypatch.setattr(llm_module, "AnthropicLLM", lambda **kw: PlanLLM())
        with pytest.raises(expect) as exc_info:
            main(
                [
                    "--config",
                    str(cfg),
                    "run",
                    "--incident",
                    "x",
                    "--tier",
                    "1",
                    "--skill-file",
                    str(skill),
                    *extra,
                ]
            )
        return getattr(exc_info.value, "code", None), calls, url

    def test_a_stop_during_an_approval_wait_ends_the_run_as_stopped(
        self, tmp_path, monkeypatch, capsys
    ):
        # Only the in-node checks act (the poll is slow): the approval wait
        # returns expired, the check after it raises inside the tier gate,
        # and LangGraph's wrapping of that error must still end as stopped.
        db_path = str(tmp_path / "cli.db")
        watcher = _stop_once_asked(db_path)
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {},
                    "justification": "read",
                },
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {},
                    "justification": "fix",
                },
            ],
            extra=("--output", str(out_file)),
        )
        watcher.join(timeout=5)
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        assert calls == []  # nothing ran, the autonomous read included
        [approval] = _approvals(url)
        assert approval.status == "expired"
        data = json.loads(out_file.read_text())
        assert data["status"] == "stopped"
        [blocked] = data["blocked_actions"]
        assert (
            blocked["block_reason"]
            == "the session was stopped during the approval wait"
        )
        assert "not sent" in data["tool_calls"][0]["error"]
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_before_a_tool_call_sends_nothing_and_audits_a_pair(
        self, tmp_path, monkeypatch, capsys
    ):
        def stop_at_plan(prompt, db_path):
            if PLAN_MARK in prompt:
                _stop_in_db(db_path, expire=False)

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "read"}],
            on_prompt=stop_at_plan,
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == []
        [session] = _sessions(url)
        assert session.status == "stopped"
        entries = [e for e in _audit(tmp_path) if e.get("tool_name") == "get_pods"]
        starts = [e for e in entries if e["entry_type"] == "tool_call_start"]
        ends = [e for e in entries if e["entry_type"] == "tool_call_end"]
        # The call that started also ended, as not sent.
        assert len(starts) == len(ends) == 1
        assert "not sent" in json.dumps(ends[0].get("result"))
        # The run ends at the stop: verify and summary ask the model nothing.
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []

    def test_a_stop_after_the_last_poll_is_not_overwritten(
        self, tmp_path, monkeypatch, capsys
    ):
        def stop_at_summary(prompt, db_path):
            if SUMMARY_MARK in prompt:
                _stop_in_db(db_path, expire=False)

        code, calls, url = self._run(
            tmp_path, monkeypatch, [], on_prompt=stop_at_summary
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_run_stopped_after_its_tools_ran_keeps_its_record(
        self, tmp_path, monkeypatch, capsys
    ):
        def stop_at_summary(prompt, db_path):
            if SUMMARY_MARK in prompt:
                _stop_in_db(db_path, expire=False)

        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "read"}],
            on_prompt=stop_at_summary,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == ["get_pods"]
        data = json.loads(out_file.read_text())
        # The record says what ran, with the stop as its status.
        assert data["status"] == "stopped"
        assert [c["tool_name"] for c in data["tool_calls"]] == ["get_pods"]
        assert "Executed: 1" in out
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_mid_node_keeps_the_calls_that_ran(
        self, tmp_path, monkeypatch, capsys
    ):
        # The stop lands while the first of two reads runs: the second is not
        # sent, the model is asked nothing more, and the record still lists
        # the read that ran.
        async def stop_during_first(tool_name, db_path):
            if len(calls_seen) == 0:
                _stop_in_db(db_path, expire=False)
            calls_seen.append(tool_name)

        calls_seen: list[str] = []
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"},
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"ns": "b"},
                    "justification": "b",
                },
            ],
            on_call=stop_during_first,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == ["get_pods"]
        data = json.loads(out_file.read_text())
        assert data["status"] == "stopped"
        assert [c["error"] is None for c in data["tool_calls"]] == [True, False]
        assert "not sent" in data["tool_calls"][1]["error"]
        assert "get_pods [ok]" in out
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []

    def test_a_stopped_run_records_its_calls_as_a_finished_run_does(
        self, tmp_path, monkeypatch, capsys
    ):
        # The stop lands during the second of three reads. The record is the
        # execute node's own: the call the server rejected shows as failed,
        # credential-shaped output is redacted, the third call is not sent,
        # and every call keeps its parameters.
        secret = "AKIA" + "ABCDEFGHIJKLMNOP"
        seen: list[str] = []

        async def stop_during_second(tool_name, db_path):
            seen.append(tool_name)
            if len(seen) == 2:
                _stop_in_db(db_path, expire=False)

        def respond(tool_name, params):
            if params.get("namespace") == "a":
                return CallToolResult(
                    isError=True,
                    content=[TextContent(type="text", text="namespace a not found")],
                )
            return CallToolResult(
                isError=False, content=[TextContent(type="text", text=f"key {secret}")]
            )

        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": ns},
                    "justification": ns,
                }
                for ns in ("a", "b", "c")
            ],
            on_call=stop_during_second,
            respond=respond,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        assert calls == ["get_pods", "get_pods"]
        written = out_file.read_text()
        assert secret not in written
        data = json.loads(written)
        assert data["status"] == "stopped"
        records = data["tool_calls"]
        assert [r["tool_parameters"] for r in records] == [
            {"namespace": "a"},
            {"namespace": "b"},
            {"namespace": "c"},
        ]
        assert records[0]["error"].startswith("the tool reported an error")
        assert "namespace a not found" in records[0]["error"]
        assert records[1]["error"] is None
        assert "[redacted secret]" in json.dumps(records[1]["result"])
        assert "not sent" in records[2]["error"]
        # The printout counts only the calls that were sent.
        assert "Executed: 2" in out and "Not sent: 1" in out
        assert "get_pods [error]" in out and "get_pods [not sent]" in out
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_before_an_approval_blocks_it_without_a_request(
        self, tmp_path, monkeypatch, capsys
    ):
        # The stop lands while the model plans: the approval is never asked
        # (no request row), the action is blocked with the stop as its reason,
        # and the read the gate let through is recorded as not sent.
        def stop_at_plan(prompt, db_path):
            if PLAN_MARK in prompt:
                _stop_in_db(db_path, expire=False)

        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "ratings-1"},
                    "justification": "fix",
                },
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": "shop"},
                    "justification": "read",
                },
            ],
            on_prompt=stop_at_plan,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == []
        assert _approvals(url) == []
        data = json.loads(out_file.read_text())
        assert data["status"] == "stopped"
        [blocked] = data["blocked_actions"]
        assert blocked["tool_name"] == "restart_pod"
        assert blocked["block_reason"] == NOT_ASKED
        [record] = data["tool_calls"]
        assert record["tool_parameters"] == {"namespace": "shop"}
        assert "not sent" in record["error"]
        assert f"restart_pod: {NOT_ASKED}" in out
        blocked_entries = [
            e for e in _audit(tmp_path) if e["entry_type"] == "tool_call_blocked"
        ]
        assert [e["tool_name"] for e in blocked_entries] == ["restart_pod"]

    def test_a_stop_during_a_skill_workflow_keeps_the_steps_that_ran(
        self, tmp_path, monkeypatch, capsys
    ):
        # The stop lands during the workflow's first step: that step's result
        # stays in the record, the approval step is blocked without a request
        # and the last step is not sent.
        async def stop_during_first(tool_name, db_path):
            if len(calls_seen) == 0:
                _stop_in_db(db_path, expire=False)
            calls_seen.append(tool_name)

        calls_seen: list[str] = []
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [],
            on_call=stop_during_first,
            skill_text=SKILL_T1 + WORKFLOW_T1,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == ["get_pods"]
        assert _approvals(url) == []
        data = json.loads(out_file.read_text())
        assert data["status"] == "stopped"
        steps = [
            (r["workflow_step_id"], r["workflow_step_status"])
            for r in data["tool_calls"]
        ]
        assert steps == [
            ("look", "completed"),
            ("fix", "blocked"),
            ("look_again", "failed"),
        ]
        assert data["tool_calls"][0]["tool_parameters"] == {"namespace": "shop"}
        assert data["tool_calls"][1]["block_reason"] == NOT_ASKED
        assert "not sent" in data["tool_calls"][2]["error"]
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_only_the_poll_sees_ends_the_run_before_the_next_request(
        self, tmp_path, monkeypatch, capsys
    ):
        # Nothing reads the row between the diagnosis and the plan, so only
        # the poll can find this stop: it lands while the model writes the
        # diagnosis, and the plan is never asked for.
        def stop_while_diagnosing(prompt, db_path):
            if DIAGNOSE_MARK in prompt:
                _stop_in_db(db_path, expire=False)
                time.sleep(0.4)

        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_prompt=stop_while_diagnosing,
            poll=0.05,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        assert any(DIAGNOSE_MARK in p for p in self.prompts)
        assert not any(PLAN_MARK in p for p in self.prompts)
        # The model request in flight at the stop completed and is kept.
        assert json.loads(out_file.read_text())["diagnosis"] == "[stub]"
        assert "--- Diagnosis ---" in out
        assert calls == []
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_an_override_hands_the_session_to_the_app_and_ends_the_run(
        self, tmp_path, monkeypatch, capsys
    ):
        # While the first approval waits, the dashboard's Override moves the
        # session to Tier 2 for the app to run: the CLI asks and sends nothing
        # more and leaves the row as the Override set it.
        db_path = str(tmp_path / "cli.db")
        watcher = _stop_once_asked(db_path, _override_in_db)
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix a",
                },
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": "shop"},
                    "justification": "read",
                },
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "b"},
                    "justification": "fix b",
                },
            ],
            extra=("--output", str(out_file)),
        )
        watcher.join(timeout=5)
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "overridden to Tier 2 from the dashboard" in out
        assert calls == []
        [approval] = _approvals(url)
        assert approval.status == "expired"
        [session] = _sessions(url)
        assert (session.status, session.tier, session.ended_at) == ("active", 2, None)
        data = json.loads(out_file.read_text())
        assert data["status"] == "stopped"
        assert [b["block_reason"] for b in data["blocked_actions"]] == [
            "the session was overridden to Tier 2 during the approval wait",
            "the session was overridden to Tier 2; the approval was not asked",
        ]
        [record] = data["tool_calls"]
        assert record["tool_parameters"] == {"namespace": "shop"}
        assert "not sent" in record["error"]
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []

    def test_a_stop_just_before_the_request_is_written_is_kept(
        self, tmp_path, monkeypatch, capsys
    ):
        # The Stop commits after the CLI's own read and before the approval
        # request is written: the request is not kept, the row stays stopped
        # and nothing is sent or asked of the model.
        from backend.db.repos import ApprovalRequestRepo

        db_path = str(tmp_path / "cli.db")
        real_create = ApprovalRequestRepo.create

        async def stop_then_create(*args, **kwargs):
            _stop_in_db(db_path)
            return await real_create(*args, **kwargs)

        monkeypatch.setattr(
            ApprovalRequestRepo, "create", staticmethod(stop_then_create)
        )
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {},
                    "justification": "read",
                },
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix",
                },
            ],
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        assert calls == []
        assert _approvals(url) == []
        [session] = _sessions(url)
        assert session.status == "stopped"
        data = json.loads(out_file.read_text())
        [blocked] = data["blocked_actions"]
        assert blocked["block_reason"] == NOT_ASKED
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []

    def test_an_interrupted_run_closes_its_row_and_expires_its_approval(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ctrl-C cancels the run's task (asyncio.run does that on the first
        # Ctrl-C). Here the task is cancelled while an approval waits.
        import cli.opsmender as cli_module

        db_path = str(tmp_path / "cli.db")
        real_run = cli_module._run_incident

        async def cancelled_once_asked(cfg, args):
            task = asyncio.ensure_future(real_run(cfg, args))
            for _ in range(400):
                if task.done():
                    break
                try:
                    if _pending_count(db_path):
                        break
                except sqlite3.OperationalError:
                    pass
                await asyncio.sleep(0.05)
            task.cancel()
            results = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(results[0], asyncio.CancelledError), results
            return 130

        monkeypatch.setattr(cli_module, "_run_incident", cancelled_once_asked)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix",
                },
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {},
                    "justification": "read",
                },
            ],
        )
        assert code == 130
        assert calls == []
        [approval] = _approvals(url)
        assert approval.status == "expired"
        [session] = _sessions(url)
        assert session.status == "stopped" and session.ended_at is not None
        assert "interrupted" in session.summary

    def test_a_stop_during_setup_is_read_before_the_first_model_request(
        self, tmp_path, monkeypatch, capsys
    ):
        # The Stop lands while the CLI connects to the MCP server, before the
        # graph starts: the model is never asked.
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_connect=lambda db_path: _stop_in_db(db_path, expire=False),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "was stopped" in out
        assert self.prompts == []
        assert calls == []
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_real_failure_in_a_run_nobody_stopped_still_fails(
        self, tmp_path, monkeypatch, capsys
    ):
        def outage(prompt, db_path):
            if PLAN_MARK in prompt:
                raise RuntimeError("model API outage")

        code, calls, url = self._run(tmp_path, monkeypatch, [], on_prompt=outage)
        out, err = capsys.readouterr()
        assert code == 1
        assert "Workflow failed: model API outage" in err
        [session] = _sessions(url)
        assert session.status == "failed" and session.ended_at is not None
        assert "model API outage" in session.summary

    def test_a_failure_after_a_stop_leaves_the_row_stopped(
        self, tmp_path, monkeypatch, capsys
    ):
        # The run is stopped at the plan, then writing --output fails (its
        # folder is a file): the failure close must not overwrite the Stop.
        def stop_at_plan(prompt, db_path):
            if PLAN_MARK in prompt:
                _stop_in_db(db_path, expire=False)

        blocker = tmp_path / "taken"
        blocker.write_text("a file, not a folder")
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [],
            on_prompt=stop_at_plan,
            extra=("--output", str(blocker / "result.json")),
        )
        out, err = capsys.readouterr()
        assert code == 1
        assert "Workflow failed" in err
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_tool_call_in_flight_is_never_cut_off(
        self, tmp_path, monkeypatch, capsys
    ):
        # The poll runs every 50 ms while the call takes 300 ms after the stop:
        # the call completes and the audit log pairs its start and end.
        async def slow_after_stop(tool_name, db_path):
            _stop_in_db(db_path, expire=False)
            await asyncio.sleep(0.3)

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_call=slow_after_stop,
            poll=0.05,
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == ["get_pods"]
        entries = [e for e in _audit(tmp_path) if e.get("tool_name") == "get_pods"]
        starts = [e for e in entries if e["entry_type"] == "tool_call_start"]
        ends = [e for e in entries if e["entry_type"] == "tool_call_end"]
        assert len(starts) == len(ends) == 1
        assert "error" not in json.dumps(ends[0].get("result") or {})
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_an_llm_setup_failure_closes_the_row(self, tmp_path, monkeypatch, capsys):
        import backend.agent.llm as llm_module

        def no_key(**kwargs):
            raise EnvironmentError("ANTHROPIC_API_KEY is not set")

        cfg, url, _org_id = _workspace_config(tmp_path)
        monkeypatch.setattr(llm_module, "AnthropicLLM", no_key)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "run", "--incident", "x", "--tier", "1"])
        assert exc_info.value.code == 1
        [session] = _sessions(url)
        assert session.status == "failed" and session.ended_at is not None
        assert "LLM setup failed" in session.summary

    def test_an_unknown_mcp_server_closes_the_row(self, tmp_path, monkeypatch, capsys):
        code, calls, url = self._run(
            tmp_path, monkeypatch, [], extra=("--mcp-server", "typo")
        )
        assert code == 1
        [session] = _sessions(url)
        assert session.status == "failed" and session.ended_at is not None
        assert "typo" in session.summary
