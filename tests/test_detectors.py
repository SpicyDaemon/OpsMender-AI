"""Tests for detector CRUD, history, and run-now behavior."""

from __future__ import annotations

import asyncio
import json
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import MCPServerRepo, ModelConfigRepo
from backend.detector.runner import DetectorRunResult, run_detector_rule
from backend.detector.scheduler import DetectorScheduler


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "detectors-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from backend.db.models import Organization
        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        f"AIM_DATABASE_URL={database_url}\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    class _FakePool:
        async def get_server(self, org_id: uuid.UUID, name: str):
            return object()

        @asynccontextmanager
        async def connect(self, org_id: uuid.UUID, name: str):
            class _Session:
                pass

            yield _Session()

    set_mcp_pool(_FakePool())
    application.dependency_overrides[get_db] = _override_get_db(factory)
    yield application

    set_env_path(None)
    await engine.dispose()


def _override_get_db(factory):
    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _get_db


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "detector-admin",
            "email": "detector-admin@test.com",
            "password": "securepass123",
        },
    )
    # Link user to TEST_ORG_ID
    factory = client._transport.app.state.session_factory
    async with factory() as db:
        from backend.db.repos import UserRepo
        user = await UserRepo.get_by_username(db, "detector-admin")
        if user:
            await UserRepo.add_to_organization(db, user.id, TEST_ORG_ID, role="admin")
            await UserRepo.set_primary_org(db, user.id, TEST_ORG_ID)
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={"username": "detector-admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _seed_server(app, *, name: str = "k8s-detector") -> str:
    async with app.state.session_factory() as db:
        server = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name=name,
            transport="stdio",
            command="echo",
        )
        await db.commit()
        await db.refresh(server)
        return str(server.id)


class TestDetectorAPI:
    async def test_list_templates(self, client: AsyncClient, auth_headers):
        resp = await client.get("/detectors/templates", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        keys = [item["key"] for item in data["items"]]
        assert "k8s_crashloop" in keys
        assert "generic_unusual_activity" in keys

    async def test_create_list_update_delete_rule(
        self, client: AsyncClient, app, auth_headers
    ):
        server_id = await _seed_server(app)

        create_resp = await client.post(
            "/detectors",
            json={
                "name": "watch-crashloops",
                "mcp_server_id": server_id,
                "prompt_template": "Find crashlooping pods",
                "interval_seconds": 600,
                "severity_default": "high",
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        rule_id = create_resp.json()["id"]

        list_resp = await client.get("/detectors", headers=auth_headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

        update_resp = await client.put(
            f"/detectors/{rule_id}",
            json={
                "interval_seconds": 900,
                "is_active": False,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["interval_seconds"] == 900
        assert update_resp.json()["is_active"] is False

        delete_resp = await client.delete(
            f"/detectors/{rule_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

    async def test_run_and_history(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        server_id = await _seed_server(app, name="history-detector")
        async with app.state.session_factory() as db:
            cfg = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="stub-default",
                provider="openai",
                model_id="gpt-4o",
                api_key_env_var="OPENAI_API_KEY",
                is_default=True,
            )
            await db.commit()

        create_resp = await client.post(
            "/detectors",
            json={
                "name": "watch-5xx",
                "mcp_server_id": server_id,
                "prompt_template": "Find elevated 5xx errors",
            },
            headers=auth_headers,
        )
        rule_id = create_resp.json()["id"]

        async def _fake_run(db, *, rule, pool, config, budget_guard=None):
            from backend.db.repos import DetectorHistoryRepo, DetectorRuleRepo

            await DetectorRuleRepo.mark_run(
                db, TEST_ORG_ID, rule.id, last_fingerprint="det-123"
            )
            await DetectorHistoryRepo.create(
                db,
                TEST_ORG_ID,
                rule_id=rule.id,
                duration_ms=87,
                issue_detected=True,
                raw_verdict={"issue_detected": True, "fingerprint": "det-123"},
            )
            return DetectorRunResult(success=True, issue_detected=True)

        monkeypatch.setattr("backend.api.routes.detectors.run_detector_rule", _fake_run)

        run_resp = await client.post(
            f"/detectors/{rule_id}/run",
            headers=auth_headers,
        )
        assert run_resp.status_code == 200
        assert run_resp.json()["success"] is True
        assert run_resp.json()["issue_detected"] is True

        history_resp = await client.get(
            f"/detectors/{rule_id}/history",
            headers=auth_headers,
        )
        assert history_resp.status_code == 200
        assert history_resp.json()["total"] == 1
        assert history_resp.json()["items"][0]["issue_detected"] is True


class TestDetectorRunner:
    async def test_runner_creates_incident_and_history(self, app, monkeypatch):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-k8s",
                transport="stdio",
                command="echo",
            )
            await db.commit()
            await db.refresh(server)

            rule = await __import__(
                "backend.db.repos", fromlist=["DetectorRuleRepo"]
            ).DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-rule",
                mcp_server_id=server.id,
                prompt_template="Detect crashlooping pods",
                severity_default="high",
            )
            await db.commit()
            await db.refresh(rule)

            class _FakeProvider:
                def __init__(self):
                    self.calls = 0

                def complete(self, prompt: str) -> str:
                    self.calls += 1
                    if self.calls == 1:
                        return json.dumps(
                            [
                                {
                                    "tool_name": "get_pods",
                                    "tool_parameters": {"namespace": "default"},
                                    "justification": "Check unhealthy pods",
                                }
                            ]
                        )
                    return json.dumps(
                        {
                            "issue_detected": True,
                            "title": "Pods crashlooping in default",
                            "severity": "high",
                            "description": "api-7d9d is in CrashLoopBackOff",
                            "fingerprint": "pods-crashloop-default",
                        }
                    )

            class _Tool:
                def __init__(self, name: str):
                    self.name = name
                    self.description = "test tool"
                    self.inputSchema = {}

            class _Content:
                def __init__(self, text: str):
                    self.text = text

            class _CallResult:
                def __init__(self, text: str):
                    self.isError = False
                    self.content = [_Content(text)]

            monkeypatch.setattr(
                "backend.detector.runner.create_provider",
                lambda **kwargs: _FakeProvider(),
            )

            async def _fake_list_tools(session):
                return [_Tool("get_pods"), _Tool("delete_pod")]

            async def _fake_call_tool(session, tool_name, arguments=None):
                assert tool_name == "get_pods"
                return _CallResult("api-7d9d CrashLoopBackOff")

            monkeypatch.setattr("backend.detector.runner.list_tools", _fake_list_tools)
            monkeypatch.setattr("backend.detector.runner.call_tool", _fake_call_tool)

            class _Pool:
                async def get_server(self, org_id: uuid.UUID, name: str):
                    return object()

                @asynccontextmanager
                async def connect(self, org_id: uuid.UUID, name: str):
                    class _Session:
                        pass

                    yield _Session()

            result = await run_detector_rule(
                db,
                rule=rule,
                pool=_Pool(),
                config=app.state.config,
            )
            await db.commit()

            assert result.success is True
            assert result.issue_detected is True
            assert result.incident_id is not None

    async def test_runner_filters_destructive_plan_actions(self, app, monkeypatch):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-filter",
                transport="stdio",
                command="echo",
            )
            await db.commit()
            await db.refresh(server)

            from backend.db.repos import DetectorRuleRepo

            rule = await DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-filter-rule",
                mcp_server_id=server.id,
                prompt_template="Check pods only",
                severity_default="medium",
            )
            await db.commit()
            await db.refresh(rule)

            class _FakeProvider:
                def __init__(self):
                    self.calls = 0

                def complete(self, prompt: str) -> str:
                    self.calls += 1
                    if self.calls == 1:
                        return json.dumps(
                            [
                                {
                                    "tool_name": "delete_pod",
                                    "tool_parameters": {"name": "api"},
                                    "justification": "bad idea",
                                },
                                {
                                    "tool_name": "get_pods",
                                    "tool_parameters": {"namespace": "default"},
                                    "justification": "safe read",
                                },
                            ]
                        )
                    return json.dumps(
                        {
                            "issue_detected": False,
                            "title": "",
                            "severity": "medium",
                            "description": "No issue detected",
                            "fingerprint": "",
                        }
                    )

            class _Tool:
                def __init__(self, name: str):
                    self.name = name
                    self.description = "test tool"
                    self.inputSchema = {}

            class _Content:
                def __init__(self, text: str):
                    self.text = text

            class _CallResult:
                def __init__(self, text: str):
                    self.isError = False
                    self.content = [_Content(text)]

            calls: list[str] = []
            monkeypatch.setattr(
                "backend.detector.runner.create_provider",
                lambda **kwargs: _FakeProvider(),
            )

            async def _fake_list_tools(session):
                return [_Tool("get_pods"), _Tool("delete_pod")]

            async def _fake_call_tool(session, tool_name, arguments=None):
                calls.append(tool_name)
                return _CallResult("safe output")

            monkeypatch.setattr("backend.detector.runner.list_tools", _fake_list_tools)
            monkeypatch.setattr("backend.detector.runner.call_tool", _fake_call_tool)

            class _Pool:
                async def get_server(self, org_id: uuid.UUID, name: str):
                    return object()

                @asynccontextmanager
                async def connect(self, org_id: uuid.UUID, name: str):
                    class _Session:
                        pass

                    yield _Session()

            result = await run_detector_rule(
                db,
                rule=rule,
                pool=_Pool(),
                config=app.state.config,
            )
            await db.commit()

            assert result.success is True
            assert calls == ["get_pods"]
            assert result.issue_detected is False

    async def test_runner_invalid_verdict_json_records_error(self, app, monkeypatch):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-invalid-json",
                transport="stdio",
                command="echo",
            )
            await db.commit()
            await db.refresh(server)

            from backend.db.repos import DetectorHistoryRepo, DetectorRuleRepo

            rule = await DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-invalid-json-rule",
                mcp_server_id=server.id,
                prompt_template="Check health",
            )
            await db.commit()
            await db.refresh(rule)

            class _FakeProvider:
                def __init__(self):
                    self.calls = 0

                def complete(self, prompt: str) -> str:
                    self.calls += 1
                    if self.calls == 1:
                        return "[]"
                    return "not-json-at-all"

            class _Tool:
                def __init__(self, name: str):
                    self.name = name
                    self.description = "test tool"
                    self.inputSchema = {}

            monkeypatch.setattr(
                "backend.detector.runner.create_provider",
                lambda **kwargs: _FakeProvider(),
            )

            async def _fake_list_tools(session):
                return [_Tool("get_pods")]

            monkeypatch.setattr("backend.detector.runner.list_tools", _fake_list_tools)

            class _Pool:
                async def get_server(self, org_id: uuid.UUID, name: str):
                    return object()

                @asynccontextmanager
                async def connect(self, org_id: uuid.UUID, name: str):
                    class _Session:
                        pass

                    yield _Session()

            result = await run_detector_rule(
                db,
                rule=rule,
                pool=_Pool(),
                config=app.state.config,
            )
            await db.commit()

            history = await DetectorHistoryRepo.list_by_rule(db, TEST_ORG_ID, rule.id)
            assert result.success is False
            assert result.incident_id is None
            assert history[0].error is not None

    async def test_runner_dedups_by_fingerprint(self, app, monkeypatch):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-dedup",
                transport="stdio",
                command="echo",
            )
            await db.commit()
            await db.refresh(server)

            from backend.db.repos import (
                DetectorHistoryRepo,
                DetectorRuleRepo,
                IncidentRepo,
            )

            rule = await DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="runner-dedup-rule",
                mcp_server_id=server.id,
                prompt_template="Detect repeated issue",
                severity_default="high",
            )
            await db.commit()
            await db.refresh(rule)

            class _FakeProvider:
                def __init__(self):
                    self.calls = 0

                def complete(self, prompt: str) -> str:
                    self.calls += 1
                    if self.calls % 2 == 1:
                        return "[]"
                    return json.dumps(
                        {
                            "issue_detected": True,
                            "title": "Repeated issue",
                            "severity": "high",
                            "description": "Same condition as before",
                            "fingerprint": "same-fingerprint",
                        }
                    )

            class _Tool:
                def __init__(self, name: str):
                    self.name = name
                    self.description = "test tool"
                    self.inputSchema = {}

            monkeypatch.setattr(
                "backend.detector.runner.create_provider",
                lambda **kwargs: _FakeProvider(),
            )

            async def _fake_list_tools(session):
                return [_Tool("get_pods")]

            monkeypatch.setattr("backend.detector.runner.list_tools", _fake_list_tools)

            class _Pool:
                async def get_server(self, org_id: uuid.UUID, name: str):
                    return object()

                @asynccontextmanager
                async def connect(self, org_id: uuid.UUID, name: str):
                    class _Session:
                        pass

                    yield _Session()

            first = await run_detector_rule(
                db,
                rule=rule,
                pool=_Pool(),
                config=app.state.config,
            )
            second = await run_detector_rule(
                db,
                rule=rule,
                pool=_Pool(),
                config=app.state.config,
            )
            await db.commit()

            incident = await IncidentRepo.get_by_external_fingerprint(
                db,
                TEST_ORG_ID,
                external_source=f"detector:{rule.id}",
                external_id="same-fingerprint",
            )
            history = await DetectorHistoryRepo.list_by_rule(db, TEST_ORG_ID, rule.id)

            assert first.success is True
            assert second.success is True
            assert first.incident_id == second.incident_id
            assert incident is not None
            assert len(history) == 2


class TestDetectorScheduler:
    async def test_tick_schedules_due_active_rule(self, app, monkeypatch):
        class _Pool:
            async def get_server(self, org_id: uuid.UUID, name: str):
                return object()

            @asynccontextmanager
            async def connect(self, org_id: uuid.UUID, name: str):
                class _Session:
                    pass

                yield _Session()

        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="scheduler-k8s",
                transport="stdio",
                command="echo",
            )
            from backend.db.repos import DetectorRuleRepo

            rule = await DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="scheduler-due",
                mcp_server_id=server.id,
                prompt_template="Detect issues",
                interval_seconds=30,
                is_active=True,
            )
            await db.commit()
            await db.refresh(rule)

            await DetectorRuleRepo.mark_run(
                db,
                TEST_ORG_ID,
                rule.id,
                last_ran_at=rule.created_at.replace(year=2020),
            )
            await db.commit()

        scheduler = DetectorScheduler(
            app.state.session_factory,
            pool=_Pool(),
            config=app.state.config,
        )

        seen: list[uuid.UUID] = []

        async def _fake_run_rule(rule_id: uuid.UUID):
            seen.append(rule_id)

        monkeypatch.setattr(scheduler, "_run_rule", _fake_run_rule)
        await scheduler._tick()
        await asyncio.sleep(0)

        assert seen == [rule.id]

    async def test_tick_skips_inactive_rule(self, app, monkeypatch):
        class _Pool:
            async def get_server(self, org_id: uuid.UUID, name: str):
                return object()

            @asynccontextmanager
            async def connect(self, org_id: uuid.UUID, name: str):
                class _Session:
                    pass

                yield _Session()

        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="scheduler-inactive-k8s",
                transport="stdio",
                command="echo",
            )
            from backend.db.repos import DetectorRuleRepo

            await DetectorRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="scheduler-inactive",
                mcp_server_id=server.id,
                prompt_template="Detect issues",
                is_active=False,
            )
            await db.commit()

        scheduler = DetectorScheduler(
            app.state.session_factory,
            pool=_Pool(),
            config=app.state.config,
        )

        seen: list[uuid.UUID] = []

        async def _fake_run_rule(rule_id: uuid.UUID):
            seen.append(rule_id)

        monkeypatch.setattr(scheduler, "_run_rule", _fake_run_rule)
        await scheduler._tick()
        await asyncio.sleep(0)

        assert seen == []
