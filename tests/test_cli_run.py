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
from backend.db.models import Organization, Session
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
            return [
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
        assert "pod" in graph["plan_tool_descriptions"]["restart_pod"]
        assert "Restart one pod" in graph["plan_tool_descriptions"]["restart_pod"]
        assert "2 tool(s) offered to the plan from lab." in out
