"""Tests for the ``opsmender run`` CLI command."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from types import SimpleNamespace

import pytest
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


class TestTier1StopFromTheDashboard:
    """M1-105: the CLI's Tier 1 session lives in the workspace, so Stop must
    end the run; before, the CLI kept going and could ask again."""

    def _run(self, tmp_path, monkeypatch, graph_body):
        cfg, url, _org_id = _workspace_config(tmp_path)
        seen: dict = {}

        class FakeGraph:
            async def ainvoke(self, state, *args, **kwargs):
                seen["session_id"] = state["session_id"]
                try:
                    await graph_body(seen, url, state["session_id"])
                except BaseException:
                    seen["cancelled"] = True
                    raise
                seen["finished"] = True
                return {"status": "completed", "summary": "ok"}

        def fake_build_graph(**kwargs):
            seen["graph"] = kwargs
            return FakeGraph()

        import backend.agent.graph as graph_module
        import cli.opsmender as cli_module

        monkeypatch.setattr(graph_module, "build_graph", fake_build_graph)
        monkeypatch.setattr(cli_module, "STOP_POLL_SECONDS", 0.05)
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
        return exc_info.value.code, seen, url

    def test_a_stop_before_the_next_approval_asks_nobody(
        self, tmp_path, monkeypatch, capsys
    ):
        async def body(seen, url, session_id):
            await _mark_stopped(url, session_id)
            await seen["graph"]["approval_service"].request_and_wait(
                session_id=uuid.UUID(session_id),
                action={"tool_name": "restart_pod", "tool_parameters": {}},
            )
            seen["asked"] = True

        code, seen, url = self._run(tmp_path, monkeypatch, body)
        out = capsys.readouterr().out
        assert code == 0
        assert "stopped from the dashboard" in out
        assert seen.get("cancelled") and "asked" not in seen
        assert _approvals(url) == []
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_during_an_approval_wait_runs_nothing_after_it(
        self, tmp_path, monkeypatch, capsys
    ):
        async def body(seen, url, session_id):
            async def stop_soon():
                await asyncio.sleep(0.3)
                await _mark_stopped(url, session_id)

            stopper = asyncio.ensure_future(stop_soon())
            await seen["graph"]["approval_service"].request_and_wait(
                session_id=uuid.UUID(session_id),
                action={"tool_name": "restart_pod", "tool_parameters": {}},
            )
            await stopper
            # The next action of the plan, an autonomous one, would run here.
            await seen["graph"]["tool_caller"](object(), "get_pods", {})
            seen["ran_after_stop"] = True

        code, seen, url = self._run(tmp_path, monkeypatch, body)
        assert code == 0
        assert seen.get("cancelled") and "ran_after_stop" not in seen
        [approval] = _approvals(url)
        assert approval.status == "expired"
        # The approval service no longer turns the stopped row into timed_out.
        [session] = _sessions(url)
        assert session.status == "stopped"

    def test_a_stop_while_the_model_works_cancels_the_run(
        self, tmp_path, monkeypatch, capsys
    ):
        async def body(seen, url, session_id):
            await _mark_stopped(url, session_id, expire_approvals=False)
            await asyncio.sleep(30)

        code, seen, url = self._run(tmp_path, monkeypatch, body)
        assert code == 0
        assert seen.get("cancelled") and "finished" not in seen
        [session] = _sessions(url)
        assert session.status == "stopped"
