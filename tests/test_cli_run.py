"""Tests for the ``opsmender run`` CLI command."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import aiosqlite
import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.llm import StubLLM
from backend.db.bootstrap import initialize_sqlite_schema
from backend.db.models import ApprovalRequest, Organization, Session
from backend.db.repos import ApprovalRequestRepo, SessionRepo
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

    def test_a_dry_run_outside_tier_1_never_asks_the_database(self, tmp_path, capsys):
        # A dry run at Tier 0, 2 or 3 opens no server and writes no session
        # row, so it has no use for the workspace (M1-105). Before, it looked
        # the workspace up anyway: on SQLite that created the database file,
        # and with a local Postgres every dry run waited on it.
        db_file = tmp_path / "never.db"
        cfg_file = tmp_path / ".env"
        cfg_file.write_text(
            f"OPSMENDER_DATABASE_URL=sqlite+aiosqlite:///{db_file.as_posix()}\n"
            f"OPSMENDER_AUDIT_LOG={(tmp_path / 'audit.jsonl').as_posix()}\n"
        )
        for tier in ("0", "2", "3"):
            with pytest.raises(SystemExit) as exc_info:
                main(
                    [
                        "--config",
                        str(cfg_file),
                        "run",
                        "--incident",
                        "x",
                        "--tier",
                        tier,
                        "--dry-run",
                    ]
                )
            assert exc_info.value.code == 0, tier
        assert not db_file.exists()

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
                if org_id is None:
                    return []  # no server in the environment
                seen["pool_org_id"] = org_id
                return [server]

            async def get_server(self, org_id=None, name=None):
                seen["pool_org_id"] = org_id
                return server

            @contextlib.asynccontextmanager
            async def connect(self, org_id, name):
                seen["opened"] = (org_id, name)
                yield object()

        @contextlib.asynccontextmanager
        async def fake_connect(config):
            raise AssertionError("a workspace server opens through the pool")
            yield

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
        # The server was looked up under the workspace, not the fallback, and
        # opened through the pool, which adds an OAuth-connected server's token.
        assert seen["pool_org_id"] == org_id
        assert seen["opened"] == (org_id, "lab")
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
        assert asked[:2] == [org_id, None]
        assert "Connecting to MCP server: env-lab" in out
        assert "Connected to env-lab." in out

    def test_a_workspace_oauth_server_opens_with_its_token(
        self, tmp_path, capsys, monkeypatch
    ):
        """An OAuth-connected server keeps its token outside its row, and only
        the pool adds it. Before, the run opened the row as it was, with no
        token, so the server refused it; and a server named from the
        environment still opens as configured."""
        from datetime import datetime, timedelta, timezone

        import backend.agent.graph as graph_module
        import backend.agent.llm as llm_module
        import backend.mcp.pool as pool_module
        import cli.opsmender as cli_module
        from backend.db.repos import MCPServerOAuthTokenRepo, MCPServerRepo

        monkeypatch.setenv("OPSMENDER_SECRET_KEY", "cli-test-secret-key-0123456789")
        cfg, url, org_id = _workspace_config(tmp_path)

        async def register():
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                row = await MCPServerRepo.create(
                    db,
                    org_id,
                    name="atlassian",
                    transport="http",
                    url="https://mcp.example.test/mcp",
                    token=None,
                    is_active=True,
                )
                await MCPServerOAuthTokenRepo.upsert(
                    db,
                    org_id,
                    mcp_server_id=row.id,
                    access_token="oauth-access",
                    refresh_token=None,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
                )
                await db.commit()
            await engine.dispose()

        asyncio.run(register())
        env_servers = [{"name": "k8s", "transport": "stdio", "command": "k8s-mcp"}]
        cfg.write_text(
            cfg.read_text() + f"OPSMENDER_MCP_SERVERS_JSON={json.dumps(env_servers)}\n"
        )
        opened: list = []

        @contextlib.asynccontextmanager
        async def fake_connect(config):
            opened.append((config.name, config.token))
            yield object()

        async def fake_list_tools(session):
            return [SimpleNamespace(name="get_pods", description="", inputSchema={})]

        class FakeGraph:
            async def ainvoke(self, state, *args, **kwargs):
                return {"status": "completed", "summary": "ok"}

        monkeypatch.setattr(pool_module, "mcp_connect", fake_connect)
        monkeypatch.setattr(cli_module, "connect", fake_connect)
        monkeypatch.setattr(cli_module, "list_tools", fake_list_tools)
        monkeypatch.setattr(graph_module, "build_graph", lambda **kw: FakeGraph())
        monkeypatch.setattr(llm_module, "AnthropicLLM", lambda **kw: StubLLM())

        for extra, expected in (
            ([], ("atlassian", "oauth-access")),
            (["--mcp-server", "atlassian"], ("atlassian", "oauth-access")),
            (["--mcp-server", "k8s"], ("k8s", None)),
        ):
            opened.clear()
            with pytest.raises(SystemExit) as exc_info:
                main(
                    ["--config", str(cfg), "run", "--incident", "x", "--tier", "2"]
                    + extra
                )
            out, err = capsys.readouterr()
            assert exc_info.value.code == 0, err
            assert opened == [expected], extra
            assert "MCP connection failed" not in err

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
# A workspace's skill for its server: one tool denied at every tier, the
# other blocked at Tier 1.
WORKSPACE_SKILL = """---
version: "1"
environment: k8s-prod
operations:
  - tool: get_pod_logs
    deny: true
    notes: "Logs carry customer data"
  - tool: get_pods
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: false, mode: blocked}
      T2: {enabled: true, mode: autonomous}
---
"""
# The local skill file with get_pod_logs allowed at every tier, so only the
# workspace's skill can refuse it.
LOGS_ALLOWED = SKILL_T1.replace(
    "operations:\n",
    "operations:\n  - tool: get_pod_logs\n    classification: safe\n"
    "    tiers:\n      T0: {enabled: true, mode: autonomous}\n"
    "      T1: {enabled: true, mode: autonomous}\n"
    "      T2: {enabled: true, mode: autonomous}\n",
)
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
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _app_run_description(url, session):
    """The incident description the app's run of *session* starts from."""
    from backend.api.session_runner import (
        _build_incident_description,
        _resolve_incident_and_messages,
    )

    async def build():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        incident, messages = await _resolve_incident_and_messages(factory, session)
        await engine.dispose()
        return _build_incident_description(incident, messages)

    return asyncio.run(build())


def _in_frames(*names):
    """Whether the running code was called from a function named *names*."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name in names:
            return True
        frame = frame.f_back
    return False


def _ctrl_c(monkeypatch, state):
    """Run the CLI's run in a task the test cancels as asyncio.run does on
    the first Ctrl-C; ``state["main"]`` is that task."""
    import cli.opsmender as cli_module

    real_run = cli_module._run_incident

    async def interruptible(cfg, args):
        task = asyncio.ensure_future(real_run(cfg, args))
        state["main"] = task
        state["loop"] = asyncio.get_running_loop()
        results = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError), results
        return 130

    monkeypatch.setattr(cli_module, "_run_incident", interruptible)


def test_the_close_leaves_a_stopped_or_overridden_row_alone(tmp_path):
    # The CLI's close skips a row the dashboard stopped and one an Override
    # gave to the app at another tier, and leaves the approvals of the app's
    # run alone.
    import cli.opsmender as cli_module
    from datetime import datetime, timedelta, timezone

    cfg, url, org_id = _workspace_config(tmp_path)

    async def scenario():
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            stopped = await SessionRepo.create(db, org_id, tier=1, status="stopped")
            overridden = await SessionRepo.create(db, org_id, tier=2)
            await ApprovalRequestRepo.create(
                db,
                org_id,
                session_id=overridden.id,
                action={"tool_name": "restart_pod", "tool_parameters": {}},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
        for row in (stopped, overridden):
            await cli_module._close_cli_session(
                factory,
                org_id,
                str(row.id),
                tier=1,
                status="failed",
                summary="the CLI failed",
            )
        async with factory() as db:
            rows = {r.id: r for r in (await db.execute(select(Session))).scalars()}
            approvals = (await db.execute(select(ApprovalRequest))).scalars().all()
        await engine.dispose()
        return rows[stopped.id], rows[overridden.id], approvals

    stopped, overridden, approvals = asyncio.run(scenario())
    assert (stopped.status, stopped.ended_at) == ("stopped", None)
    assert (overridden.status, overridden.tier, overridden.ended_at) == (
        "active",
        2,
        None,
    )
    assert [a.status for a in approvals] == ["pending"]


def _register_server(name, *, command="k", skill=None):
    """A prepare hook for the real-graph runs: the workspace registers the
    server *name*, with *skill* as its own skill when given."""
    from backend.db.repos import MCPServerRepo, SkillRepo

    def register(cfg, url, org_id):
        async def rows():
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                row = await MCPServerRepo.create(
                    db, org_id, name=name, transport="stdio", command=command
                )
                if skill is not None:
                    await SkillRepo.create(
                        db,
                        org_id,
                        name=f"{name} policy",
                        content_md=skill,
                        mcp_server_id=row.id,
                    )
                await db.commit()
            await engine.dispose()

        asyncio.run(rows())

    return register


# Two reads the workspace's skill refuses: get_pod_logs is denied and get_pods
# is blocked at Tier 1.
BOTH_READS = [
    {
        "tool_name": "get_pod_logs",
        "tool_parameters": {"pod": "a"},
        "justification": "read",
    },
    {"tool_name": "get_pods", "tool_parameters": {}, "justification": "read"},
]


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
        incident="x",
        real_pool=False,
        prepare=None,
        tools=("get_pods", "restart_pod"),
    ):
        cfg, url, _org_id = _workspace_config(tmp_path)
        if prepare is not None:
            prepare(cfg, url, _org_id)
        self.connected: list = []
        skill = tmp_path / "SKILL.md"
        skill.write_text(skill_text)
        calls: list[str] = []
        prompts: list[str] = []
        self.prompts = prompts
        server = SimpleNamespace(name="lab", transport="http")

        @contextlib.asynccontextmanager
        async def fake_connect(*args):
            self.connected.append(args)
            if on_connect is not None:
                on_connect(_db_path(url))
            # A cancel that on_connect asks for lands here, inside the connect.
            await asyncio.sleep(0)
            yield object()

        class FakePool:
            def __init__(self, session_factory, env_fallback=None):
                pass

            async def list_servers(self, org_id=None, *, active_only=True):
                return [server]

            async def get_server(self, org_id=None, name=None):
                return server if name == "lab" else None

            def connect(self, org_id, name):
                return fake_connect(org_id, name)

        async def fake_list_tools(session):
            return [
                SimpleNamespace(name=name, description=name, inputSchema={})
                for name in tools
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
        import backend.mcp.pool as pool_module
        import cli.opsmender as cli_module

        if real_pool:
            monkeypatch.setattr(pool_module, "mcp_connect", fake_connect)
        else:
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
                    incident,
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
        # The printout shows the blocked step as blocked, not as run.
        assert "Blocked:  1" in out
        assert "Executed: 1" in out and "Not sent: 1" in out
        assert f"✗ restart_pod [blocked]: {NOT_ASKED}" in out
        assert "✓ restart_pod" not in out
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
            incident="checkout pods crashlooping in shop",
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
        # The app's run works on the CLI's incident, which is the session's
        # briefing; before, the app's run had no incident at all.
        assert "checkout pods crashlooping in shop" in _app_run_description(
            url, session
        )
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

    @pytest.mark.parametrize("query", ["approval_wait", "stop_poll"])
    def test_a_ctrl_c_during_a_query_still_closes_the_row(
        self, tmp_path, monkeypatch, capsys, query
    ):
        # Ctrl-C lands while a query runs: the approval wait's read of its
        # request, or the stop watch's own read of the row. On SQLite a query
        # cut off midway keeps its lock, so the close could not commit after
        # it, and the row stayed awaiting approval with its approval pending.
        db_path = str(tmp_path / "cli.db")
        state: dict = {"main": None, "fired": False}
        if query == "approval_wait":
            table, callers = "FROM APPROVAL_REQUESTS", ("request_and_wait",)
        else:
            table, callers = "FROM SESSIONS", ("is_stopped", "_read_row")
        real_execute = aiosqlite.Cursor.execute

        async def execute(cursor, sql, parameters=None):
            if (
                not state["fired"]
                and state["main"] is not None
                and sql.lstrip().upper().startswith("SELECT")
                and table in sql.upper()
                and _in_frames(*callers)
                and _pending_count(db_path)
            ):
                state["fired"] = True
                state["main"].cancel()
            return await real_execute(cursor, sql, parameters)

        monkeypatch.setattr(aiosqlite.Cursor, "execute", execute)
        _ctrl_c(monkeypatch, state)
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
            poll=0.05 if query == "stop_poll" else 60.0,
        )
        assert state["fired"]
        assert code == 130
        assert calls == []
        [approval] = _approvals(url)
        assert approval.status == "expired"
        [session] = _sessions(url)
        assert session.status == "stopped" and "interrupted" in session.summary

    def test_a_ctrl_c_as_the_run_closes_keeps_its_end(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ctrl-C lands as the finished run closes its row: the close completes,
        # the row keeps its own end, and the audit log has one session end.
        import cli.opsmender as cli_module

        state: dict = {"main": None}
        real_close = cli_module._close_cli_session

        async def close_as_ctrl_c_lands(*args, **kwargs):
            if kwargs.get("status") == "completed":
                state["main"].cancel()
            await real_close(*args, **kwargs)

        monkeypatch.setattr(cli_module, "_close_cli_session", close_as_ctrl_c_lands)
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
        )
        assert code == 130
        assert calls == ["get_pods"]
        [session] = _sessions(url)
        assert (session.status, session.summary) == ("completed", "[stub]")
        ends = [e for e in _audit(tmp_path) if e["entry_type"] == "session_end"]
        assert len(ends) == 1

    def test_a_ctrl_c_before_the_run_starts_closes_its_row_without_an_end(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ctrl-C lands while the CLI connects to the MCP server: the row is
        # closed as interrupted, and the audit log gets no session end for a
        # session it never started.
        state: dict = {"main": None}
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_connect=lambda db_path: state["main"].cancel(),
        )
        assert code == 130
        assert self.prompts == [] and calls == []
        [session] = _sessions(url)
        assert session.status == "stopped" and "interrupted" in session.summary
        assert [e["entry_type"] for e in _audit(tmp_path)] == []

    def test_a_failure_during_an_approval_wait_expires_the_approval(
        self, tmp_path, monkeypatch, capsys
    ):
        # The wait's poll fails (the database is locked, say): the run fails,
        # and its approval expires with the row, so approving it later cannot
        # reopen a run nobody drives.
        import cli.opsmender as cli_module
        from backend.approvals.service import ApprovalService

        async def locked(seconds):
            raise RuntimeError("database is locked")

        class FailingPoll(ApprovalService):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, sleep_fn=locked, **kwargs)

        monkeypatch.setattr(cli_module, "ApprovalService", FailingPoll)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix",
                }
            ],
        )
        out, err = capsys.readouterr()
        assert code == 1
        assert "database is locked" in err
        [approval] = _approvals(url)
        assert approval.status == "expired"
        [session] = _sessions(url)
        assert session.status == "failed"

    def test_a_stop_during_the_only_approval_asks_the_model_nothing_more(
        self, tmp_path, monkeypatch, capsys
    ):
        # The plan's only action waits for approval when the Stop lands. No
        # tool call follows to read the row, so the read after the wait is
        # what keeps the model from being asked for the summary.
        import cli.opsmender as cli_module

        db_path = str(tmp_path / "cli.db")
        watcher = _stop_once_asked(db_path)
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix",
                }
            ],
            extra=("--output", str(out_file)),
        )
        watcher.join(timeout=5)
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == []
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []
        data = json.loads(out_file.read_text())
        assert data["summary"] == cli_module.STOPPED_MESSAGE

    @pytest.mark.parametrize("act", ["stop", "override"])
    def test_a_stop_the_service_finds_as_it_writes_asks_the_model_nothing_more(
        self, tmp_path, monkeypatch, capsys, act
    ):
        # The Stop (or the Override) commits after the CLI's read and before
        # the service writes the request, and the plan's only action is that
        # approval: the service refuses to ask, and the model must not be
        # asked for the summary either.
        db_path = str(tmp_path / "cli.db")
        real_create = ApprovalRequestRepo.create

        async def act_then_create(*args, **kwargs):
            (_stop_in_db if act == "stop" else _override_in_db)(db_path)
            return await real_create(*args, **kwargs)

        monkeypatch.setattr(
            ApprovalRequestRepo, "create", staticmethod(act_then_create)
        )
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "restart_pod",
                    "tool_parameters": {"pod": "a"},
                    "justification": "fix",
                }
            ],
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == []
        assert _approvals(url) == []
        plan_at = next(i for i, p in enumerate(self.prompts) if PLAN_MARK in p)
        assert self.prompts[plan_at + 1 :] == []

    def test_a_workspace_server_runs_under_the_skill_the_workspace_gave_it(
        self, tmp_path, monkeypatch, capsys
    ):
        # The workspace's skill for its server denies one tool and blocks the
        # other at Tier 1; the local skill file allows both. Before, the CLI
        # opened the workspace's server under the local file, and both ran.
        # The state --output writes names the skill that governed the run.
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            BOTH_READS,
            real_pool=True,
            prepare=_register_server("k8s-prod", skill=WORKSPACE_SKILL),
            tools=("get_pod_logs", "get_pods"),
            skill_text=LOGS_ALLOWED,
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "Connecting to MCP server: k8s-prod" in out
        assert "the workspace's skill for k8s-prod" in out
        assert calls == []
        assert "Blocked:  2" in out
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["skill_definition_path"] == "the workspace's skill for k8s-prod"

    def test_a_workspace_server_named_like_an_environment_server_keeps_its_skill(
        self, tmp_path, monkeypatch, capsys
    ):
        # The workspace and the environment both have a "k8s". The run tells
        # the workspace's server from the environment's by identity, not by
        # name, so it opens through the pool under the workspace's skill.
        register = _register_server("k8s", command="ws-k8s", skill=WORKSPACE_SKILL)

        def prepare(cfg, url, org_id):
            register(cfg, url, org_id)
            servers = [{"name": "k8s", "transport": "stdio", "command": "env-k8s"}]
            cfg.write_text(
                cfg.read_text() + f"OPSMENDER_MCP_SERVERS_JSON={json.dumps(servers)}\n"
            )

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            BOTH_READS,
            real_pool=True,
            prepare=prepare,
            tools=("get_pod_logs", "get_pods"),
            skill_text=LOGS_ALLOWED,
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        [(opened,)] = self.connected
        assert opened.command == "ws-k8s"
        assert "the workspace's skill for k8s" in out
        assert calls == []
        assert "Blocked:  2" in out

    def test_a_workspace_server_with_no_skill_runs_under_the_skill_file(
        self, tmp_path, monkeypatch, capsys
    ):
        # The workspace gave its server no skill and has no global one, so
        # --skill-file governs the run, and the saved state names that file.
        out_file = tmp_path / "result.json"
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "read"}],
            real_pool=True,
            prepare=_register_server("k8s-prod"),
            extra=("--output", str(out_file)),
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "Connected to k8s-prod." in out
        assert "the workspace's skill" not in out
        assert calls == ["get_pods"]
        saved = json.loads(out_file.read_text(encoding="utf-8"))
        assert saved["skill_definition_path"] == str(tmp_path / "SKILL.md")

    def test_a_failed_workspace_skill_lookup_opens_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        # The workspace's skill for its server cannot be read: the run opens
        # nothing, rather than the server under the local file.
        from backend.db.repos import SkillRepo

        async def hiccup(*args, **kwargs):
            raise RuntimeError("database hiccup")

        monkeypatch.setattr(SkillRepo, "get_for_mcp_server", staticmethod(hiccup))
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            BOTH_READS,
            real_pool=True,
            prepare=_register_server("k8s-prod", skill=WORKSPACE_SKILL),
            tools=("get_pod_logs", "get_pods"),
            skill_text=LOGS_ALLOWED,
        )
        out, err = capsys.readouterr()
        assert "MCP connection failed: database hiccup" in err
        assert self.connected == []
        assert calls == []

    def test_a_named_server_the_workspace_deactivated_is_not_opened(
        self, tmp_path, monkeypatch, capsys
    ):
        from backend.db.repos import MCPServerRepo

        def register(cfg, url, org_id):
            async def rows():
                engine = create_async_engine(url)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as db:
                    await MCPServerRepo.create(
                        db,
                        org_id,
                        name="retired",
                        transport="stdio",
                        command="k",
                        is_active=False,
                    )
                    await db.commit()
                await engine.dispose()

            asyncio.run(rows())

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [],
            real_pool=True,
            prepare=register,
            extra=("--mcp-server", "retired"),
        )
        out, err = capsys.readouterr()
        assert code == 1
        assert "not an active server" in err
        assert self.connected == []
        [session] = _sessions(url)
        assert session.status == "failed" and "retired" in session.summary

    def test_a_failed_workspace_lookup_opens_the_environment_server_as_configured(
        self, tmp_path, monkeypatch, capsys
    ):
        # The workspace lookup fails, so the pool hands back the environment's
        # server: it opens as configured, not through the pool under the
        # workspace, where it does not exist.
        from backend.db.repos import MCPServerRepo

        def env_server(cfg, url, org_id):
            servers = [{"name": "k8s", "transport": "stdio", "command": "k8s-mcp"}]
            cfg.write_text(
                cfg.read_text() + f"OPSMENDER_MCP_SERVERS_JSON={json.dumps(servers)}\n"
            )

        async def unreachable(*args, **kwargs):
            raise RuntimeError("database unreachable")

        monkeypatch.setattr(MCPServerRepo, "list_all", staticmethod(unreachable))
        code, calls, url = self._run(
            tmp_path, monkeypatch, [], real_pool=True, prepare=env_server
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert "Connected to k8s." in out
        assert "MCP connection failed" not in err

    @pytest.mark.parametrize("how", ["reply", "raise"])
    def test_a_server_error_never_reads_as_a_call_held_back(
        self, tmp_path, monkeypatch, capsys, how
    ):
        # The server's own error text ends like a held-back call's; nobody
        # stopped the run, so the call was sent and is counted as executed.
        spoof = "the session was stopped; the call was not sent"

        def reply(tool_name, params):
            return CallToolResult(
                isError=True, content=[TextContent(type="text", text=spoof)]
            )

        async def raise_it(tool_name, db_path):
            raise RuntimeError(spoof)

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            respond=reply if how == "reply" else None,
            on_call=raise_it if how == "raise" else None,
        )
        out, err = capsys.readouterr()
        assert code == 0, err
        assert calls == ["get_pods"]
        assert "Executed: 1" in out
        assert "Not sent" not in out and "[not sent]" not in out
        assert "✗ get_pods [error]" in out

    def test_a_server_error_before_a_held_back_call_keeps_its_own_label(
        self, tmp_path, monkeypatch, capsys
    ):
        # The server answers the first call with the held-back text as a
        # person presses Stop, and the run holds back the second call of the
        # same tool. The call the server received reads as an error and the
        # one never sent as not sent, in the order they were planned.
        spoof = "the session was stopped; the call was not sent"

        async def stop_and_fail(tool_name, db_path):
            _stop_in_db(db_path)
            raise RuntimeError(spoof)

        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": "a"},
                    "justification": "a",
                },
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": "b"},
                    "justification": "b",
                },
            ],
            on_call=stop_and_fail,
        )
        out, err = capsys.readouterr()
        assert calls == ["get_pods"]
        assert "Executed: 1" in out and "Not sent: 1" in out
        assert out.index("✗ get_pods [error]") < out.index("- get_pods [not sent]")

    def test_a_ctrl_c_sends_nothing_more_while_the_row_closes(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ctrl-C lands as the model answers the plan, and the close takes a
        # while: the graph goes on to its tool call meanwhile, which must not
        # be sent although the row still reads active. The session's end is
        # the audit log's last entry.
        import cli.opsmender as cli_module

        state: dict = {"main": None}
        real_close = cli_module._close_cli_session

        async def slow_close(*args, **kwargs):
            if kwargs.get("status") == "stopped":
                await asyncio.sleep(0.5)
            await real_close(*args, **kwargs)

        def ctrl_c_at_plan(prompt, db_path):
            if PLAN_MARK in prompt:
                state["loop"].call_soon_threadsafe(state["main"].cancel)

        monkeypatch.setattr(cli_module, "_close_cli_session", slow_close)
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_prompt=ctrl_c_at_plan,
        )
        assert code == 130
        assert calls == []
        [session] = _sessions(url)
        assert session.status == "stopped" and "interrupted" in session.summary
        entries = _audit(tmp_path)
        assert entries[-1]["entry_type"] == "session_end"
        assert [e["entry_type"] for e in entries].count("session_end") == 1

    def test_a_ctrl_c_during_the_tool_calls_read_sends_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ctrl-C lands while the tool call's own read of the row runs, and the
        # close takes a while, so that read finds the row still active. The
        # read must not clear the interruption: the call is not sent.
        import cli.opsmender as cli_module

        state: dict = {"main": None, "planned": False, "fired": False}
        real_execute = aiosqlite.Cursor.execute
        real_close = cli_module._close_cli_session

        async def execute(cursor, sql, parameters=None):
            if (
                state["planned"]
                and not state["fired"]
                and sql.lstrip().upper().startswith("SELECT")
                and "FROM SESSIONS" in sql.upper()
                and _in_frames("_read_row", "is_stopped")
            ):
                state["fired"] = True
                state["loop"].call_soon(state["main"].cancel)
                # The interruption is set before this read returns.
                await asyncio.sleep(0.05)
            return await real_execute(cursor, sql, parameters)

        async def slow_close(*args, **kwargs):
            if kwargs.get("status") == "stopped":
                await asyncio.sleep(0.5)
            await real_close(*args, **kwargs)

        def planned(prompt, db_path):
            if PLAN_MARK in prompt:
                state["planned"] = True

        monkeypatch.setattr(aiosqlite.Cursor, "execute", execute)
        monkeypatch.setattr(cli_module, "_close_cli_session", slow_close)
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            on_prompt=planned,
        )
        assert state["fired"]
        assert code == 130
        assert calls == []
        [session] = _sessions(url)
        assert session.status == "stopped" and "interrupted" in session.summary

    def test_a_ctrl_c_while_the_row_is_written_closes_it(
        self, tmp_path, monkeypatch, capsys
    ):
        import cli.opsmender as cli_module

        state: dict = {"main": None}
        real_create = cli_module.SessionMessageRepo.create

        async def ctrl_c_then_create(*args, **kwargs):
            state["main"].cancel()
            return await real_create(*args, **kwargs)

        monkeypatch.setattr(
            cli_module.SessionMessageRepo, "create", staticmethod(ctrl_c_then_create)
        )
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(tmp_path, monkeypatch, [])
        assert code == 130
        assert self.prompts == [] and calls == []
        [session] = _sessions(url)
        assert session.status == "stopped" and "interrupted" in session.summary

    def test_a_ctrl_c_as_an_unknown_server_closes_the_row_keeps_its_end(
        self, tmp_path, monkeypatch, capsys
    ):
        import cli.opsmender as cli_module

        state: dict = {"main": None}
        real_close = cli_module._close_cli_session

        async def close_as_ctrl_c_lands(*args, **kwargs):
            if kwargs.get("status") == "failed":
                state["main"].cancel()
            await real_close(*args, **kwargs)

        monkeypatch.setattr(cli_module, "_close_cli_session", close_as_ctrl_c_lands)
        _ctrl_c(monkeypatch, state)
        code, calls, url = self._run(
            tmp_path, monkeypatch, [], extra=("--mcp-server", "typo")
        )
        assert code == 130
        [session] = _sessions(url)
        assert session.status == "failed" and "typo" in session.summary

    def test_a_failure_after_the_run_closed_keeps_its_end(
        self, tmp_path, monkeypatch, capsys
    ):
        # Nobody stops the run; writing --output fails after the run closed
        # its row as completed: the row keeps that end and the audit log has
        # one session end.
        blocker = tmp_path / "taken"
        blocker.write_text("a file, not a folder")
        code, calls, url = self._run(
            tmp_path,
            monkeypatch,
            [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "a"}],
            extra=("--output", str(blocker / "result.json")),
        )
        out, err = capsys.readouterr()
        assert code == 1
        assert "Workflow failed" in err
        [session] = _sessions(url)
        assert (session.status, session.summary) == ("completed", "[stub]")
        ends = [e for e in _audit(tmp_path) if e["entry_type"] == "session_end"]
        assert len(ends) == 1

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
