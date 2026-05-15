"""Tests for the Auditor (Sprint 32) — analyzers, repos, runner, and API."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.auditor.analyzers import EnvironmentScanAnalyzer
from backend.auditor.example_analyzers import (
    IstioctlAnalyzeAnalyzer,
    KubeScoreAnalyzer,
)
from backend.auditor.base import AnalyzerContext, FindingDraft
from backend.auditor.registry import (
    _reset_for_tests,
    get_analyzer,
    list_analyzers,
    register_analyzer,
)
from backend.auditor.runner import run_audit
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import AuditFindingRepo, AuditRunRepo


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "audits-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        )
        await session.commit()
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    class _FakePool:
        async def get_server(self, org_id, name):
            return object()

        @asynccontextmanager
        async def connect(self, org_id, name):
            class _S:
                pass

            yield _S()

    set_mcp_pool(_FakePool())

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db
    yield application

    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "audit-admin",
            "email": "audit-admin@test.com",
            "password": "securepass123",
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": "audit-admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Analyzer parsing (pure unit tests — no MCP / DB needed)
# ---------------------------------------------------------------------------


class TestAnalyzerParsing:
    def test_kube_score_parses_failed_checks(self):
        raw = json.dumps(
            [
                {
                    "object_meta": {"name": "api", "namespace": "prod"},
                    "type_meta": {"kind": "Deployment"},
                    "checks": [
                        {
                            "check_name": "container-resources",
                            "grade": 1,
                            "comments": [
                                {
                                    "summary": "No CPU limit set",
                                    "description": "Containers should set CPU limits",
                                }
                            ],
                        },
                        {"check_name": "passing-check", "grade": 10},
                    ],
                }
            ]
        )
        findings = KubeScoreAnalyzer().parse(raw)
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "critical"
        assert f.resource == "Deployment/api in ns prod"
        assert "CPU limit" in f.message
        assert f.category == "container-resources"

    def test_kube_score_handles_empty_and_non_json(self):
        assert KubeScoreAnalyzer().parse("") == []
        findings = KubeScoreAnalyzer().parse("not json output")
        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_istioctl_analyze_parses_diagnostics(self):
        raw = json.dumps(
            [
                {
                    "code": "IST0101",
                    "level": "Error",
                    "message": "Referenced host not found",
                    "origin": "VirtualService prod/frontend",
                },
                {
                    "code": "IST0102",
                    "level": "Warning",
                    "message": "Deprecated annotation",
                    "origin": "Deployment prod/api",
                },
            ]
        )
        findings = IstioctlAnalyzeAnalyzer().parse(raw, namespace="prod")
        assert len(findings) == 2
        assert findings[0].severity == "high"
        assert findings[0].category == "IST0101"
        assert findings[1].severity == "medium"

    def test_istioctl_analyze_handles_messages_envelope(self):
        raw = json.dumps(
            {"messages": [{"code": "IST0001", "level": "Info", "message": "OK"}]}
        )
        findings = IstioctlAnalyzeAnalyzer().parse(raw)
        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_generic_llm_parses_json_array(self):
        raw = json.dumps(
            [
                {
                    "severity": "high",
                    "category": "memory",
                    "resource": "pod/api-1",
                    "message": "OOM observed in last hour",
                    "suggested_fix": "Increase memory limit",
                }
            ]
        )
        findings = EnvironmentScanAnalyzer().parse(raw)
        assert len(findings) == 1
        assert findings[0].severity == "high"
        assert findings[0].suggested_fix == "Increase memory limit"

    def test_generic_llm_strips_code_fences(self):
        raw = "```json\n[{\"severity\":\"low\",\"message\":\"hi\"}]\n```"
        findings = EnvironmentScanAnalyzer().parse(raw)
        assert len(findings) == 1
        assert findings[0].message == "hi"

    def test_finding_draft_normalizes_unknown_severity(self):
        draft = FindingDraft(analyzer="x", severity="bogus", message="m")
        assert draft.normalized_severity() == "info"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_default_analyzer_registered(self):
        keys = {s.key for s in list_analyzers()}
        # Only the platform-agnostic environment-scan is registered by
        # default. Example platform-specific analyzers must be opted into
        # by the operator at startup.
        assert "environment-scan" in keys

    def test_legacy_analyzers_not_auto_registered(self):
        keys = {s.key for s in list_analyzers()}
        assert "kube-score" not in keys
        assert "istioctl-analyze" not in keys

    def test_get_analyzer_returns_none_for_unknown(self):
        assert get_analyzer("does-not-exist") is None


# ---------------------------------------------------------------------------
# Repository smoke tests
# ---------------------------------------------------------------------------


class TestRepos:
    async def test_audit_run_create_and_list(self, app):
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            await db.commit()
            assert run.status == "queued"
            assert run.finding_count == 0

            listed = await AuditRunRepo.list_all(db, TEST_ORG_ID)
            assert len(listed) == 1
            assert listed[0].id == run.id

    async def test_audit_finding_filtering(self, app):
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            await AuditFindingRepo.create(
                db,
                TEST_ORG_ID,
                run_id=run.id,
                analyzer="kube-score",
                severity="high",
                message="msg-1",
            )
            await AuditFindingRepo.create(
                db,
                TEST_ORG_ID,
                run_id=run.id,
                analyzer="kube-score",
                severity="low",
                message="msg-2",
            )
            await db.commit()

            highs = await AuditFindingRepo.list_filtered(
                db, TEST_ORG_ID, severity="high"
            )
            assert len(highs) == 1
            assert highs[0].message == "msg-1"


# ---------------------------------------------------------------------------
# Runner (orchestration)
# ---------------------------------------------------------------------------


class _FakeAnalyzer:
    """Minimal Analyzer-compatible stub used by runner tests."""

    def __init__(self, key, drafts=None, raise_exc=None):
        self.key = key
        self.label = key
        self.description = ""
        self._drafts = drafts or []
        self._raise = raise_exc

    async def run(self, ctx):
        if self._raise is not None:
            raise self._raise
        return list(self._drafts)


class TestRunner:
    async def test_runner_persists_findings_and_marks_completed(self, app):
        fake = _FakeAnalyzer(
            "fake",
            drafts=[
                FindingDraft(
                    analyzer="fake",
                    severity="high",
                    message="boom",
                    resource="pod/x",
                )
            ],
        )
        register_analyzer(fake)

        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["fake"]
            )
            await db.commit()
            run_id = run.id

        async with app.state.session_factory() as db:
            count = await run_audit(
                db,
                run_id=run_id,
                org_id=TEST_ORG_ID,
                pool=None,
                config=app.state.config,
            )
            await db.commit()
            assert count == 1

        async with app.state.session_factory() as db:
            refreshed = await AuditRunRepo.get_by_id(db, TEST_ORG_ID, run_id)
            assert refreshed.status == "completed"
            assert refreshed.finding_count == 1
            findings = await AuditFindingRepo.list_by_run(
                db, TEST_ORG_ID, run_id
            )
            assert len(findings) == 1
            assert findings[0].severity == "high"

        # cleanup — re-register the default analyzer so other tests stay green
        _reset_for_tests()
        import backend.auditor as _a  # noqa: F401
        from backend.auditor import analyzers as _an

        register_analyzer(_an.EnvironmentScanAnalyzer())

    async def test_runner_captures_analyzer_exception_as_info(self, app):
        fake = _FakeAnalyzer("boomer", raise_exc=RuntimeError("network down"))
        register_analyzer(fake)

        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["boomer"]
            )
            await db.commit()
            run_id = run.id

        async with app.state.session_factory() as db:
            await run_audit(
                db,
                run_id=run_id,
                org_id=TEST_ORG_ID,
                pool=None,
                config=app.state.config,
            )
            await db.commit()

        async with app.state.session_factory() as db:
            findings = await AuditFindingRepo.list_by_run(
                db, TEST_ORG_ID, run_id
            )
            assert len(findings) == 1
            assert findings[0].severity == "info"
            assert "network down" in findings[0].message
            refreshed = await AuditRunRepo.get_by_id(db, TEST_ORG_ID, run_id)
            # Single analyzer that raised → run marked failed
            assert refreshed.status == "failed"

        _reset_for_tests()
        from backend.auditor import analyzers as _an

        register_analyzer(_an.EnvironmentScanAnalyzer())


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


class TestAuditAPI:
    async def test_list_analyzers_endpoint(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get("/audits/analyzers", headers=auth_headers)
        assert resp.status_code == 200
        keys = {item["key"] for item in resp.json()["items"]}
        # Only the platform-agnostic default analyzer ships registered.
        assert "environment-scan" in keys

    async def test_create_run_rejects_unknown_analyzer(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/audits/runs",
            json={"analyzers": ["does-not-exist"], "execute": False},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_create_run_without_execute_queues(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/audits/runs",
            json={"analyzers": ["environment-scan"], "execute": False},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "queued"
        assert body["analyzers"] == ["environment-scan"]
        assert body["finding_count"] == 0

    async def test_run_detail_includes_findings(
        self, client: AsyncClient, app, auth_headers
    ):
        # Seed a run + finding directly via repo
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            await AuditFindingRepo.create(
                db,
                TEST_ORG_ID,
                run_id=run.id,
                analyzer="kube-score",
                severity="medium",
                message="seed-finding",
            )
            await db.commit()
            run_id = str(run.id)

        resp = await client.get(
            f"/audits/runs/{run_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run"]["id"] == run_id
        assert len(body["findings"]) == 1
        assert body["findings"][0]["message"] == "seed-finding"

    async def test_dismiss_finding(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            finding = await AuditFindingRepo.create(
                db,
                TEST_ORG_ID,
                run_id=run.id,
                analyzer="kube-score",
                severity="low",
                message="latent",
            )
            await db.commit()
            finding_id = str(finding.id)

        resp = await client.post(
            f"/audits/findings/{finding_id}/dismiss",
            json={"reason": "Known false positive"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"
        assert resp.json()["dismiss_reason"] == "Known false positive"

    async def test_remediate_creates_session_and_updates_status(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            finding = await AuditFindingRepo.create(
                db,
                TEST_ORG_ID,
                run_id=run.id,
                analyzer="kube-score",
                severity="high",
                message="something broken",
            )
            await db.commit()
            finding_id = str(finding.id)

        resp = await client.post(
            f"/audits/findings/{finding_id}/remediate",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "remediating"
        assert "session_id" in body

        # Cannot remediate an already-dismissed finding.
        async with app.state.session_factory() as db:
            await AuditFindingRepo.update_status(
                db, TEST_ORG_ID, finding.id, status="dismissed"
            )
            await db.commit()
        resp = await client.post(
            f"/audits/findings/{finding_id}/remediate",
            headers=auth_headers,
        )
        assert resp.status_code == 409

    async def test_finding_filtering_endpoint(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            run = await AuditRunRepo.create(
                db, TEST_ORG_ID, analyzers=["kube-score"]
            )
            for sev in ("high", "low"):
                await AuditFindingRepo.create(
                    db,
                    TEST_ORG_ID,
                    run_id=run.id,
                    analyzer="kube-score",
                    severity=sev,
                    message=f"sev-{sev}",
                )
            await db.commit()

        resp = await client.get(
            "/audits/findings?severity=high", headers=auth_headers
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(item["severity"] == "high" for item in items)
        assert any(item["message"] == "sev-high" for item in items)
