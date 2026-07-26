"""Tests for `opsmender doctor` checks (Sprint 43 P0 #3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import doctor
from backend.config_loader import (
    AppConfig,
    AuditConfig,
    AuthConfig,
    IngestConfig,
    AppSettings,
)
from backend.db.models import Base


def _config(
    *,
    secret: str = "x" * 64,
    static_dir: str = "./frontend/out",
    audit_path: str = "./logs/audit.jsonl",
) -> AppConfig:
    cfg = AppConfig.__new__(AppConfig)
    cfg.auth = AuthConfig(jwt_secret=secret)
    cfg.audit = AuditConfig(output=audit_path)
    cfg.ingest = IngestConfig(rate_limit=60, rate_window=60)
    settings = AppSettings.__new__(AppSettings)
    settings.frontend_static_dir = static_dir
    cfg.app = settings
    return cfg


# ---------------------------------------------------------------------------
# Pure (non-DB) checks
# ---------------------------------------------------------------------------


class TestJwtSecretCheck:
    def test_strong_secret_passes(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
        r = doctor.check_jwt_secret(_config(secret="x" * 64))
        assert r.status == "ok"

    def test_placeholder_in_prod_fails(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
        r = doctor.check_jwt_secret(_config(secret="change-me-in-production"))
        assert r.status == "fail"
        assert "placeholder" in r.detail

    def test_placeholder_in_dev_warns(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "development")
        r = doctor.check_jwt_secret(_config(secret="change-me-in-production"))
        assert r.status == "warn"

    def test_short_secret_in_prod_warns(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
        r = doctor.check_jwt_secret(_config(secret="abc"))
        assert r.status == "warn"


class TestFrontendStaticCheck:
    def test_missing_dir_warns(self, tmp_path):
        cfg = _config(static_dir=str(tmp_path / "nope"))
        r = doctor.check_frontend_static(cfg)
        assert r.status == "warn"

    def test_directory_with_index_passes(self, tmp_path):
        (tmp_path / "index.html").write_text("<html />")
        cfg = _config(static_dir=str(tmp_path))
        r = doctor.check_frontend_static(cfg)
        assert r.status == "ok"

    def test_directory_missing_index_warns(self, tmp_path):
        cfg = _config(static_dir=str(tmp_path))
        r = doctor.check_frontend_static(cfg)
        assert r.status == "warn"


class TestAuditLogCheck:
    def test_writeable_path_passes(self, tmp_path):
        cfg = _config(audit_path=str(tmp_path / "audit.jsonl"))
        r = doctor.check_audit_log(cfg)
        assert r.status == "ok"

    def test_unwriteable_path_fails(self, tmp_path):
        cfg = _config(audit_path=str(tmp_path / "audit.jsonl"))
        with patch("pathlib.Path.write_text", side_effect=OSError("EACCES")):
            r = doctor.check_audit_log(cfg)
        assert r.status == "fail"


class TestExitCode:
    def test_all_ok_returns_zero(self):
        results = [
            doctor.CheckResult("a", "ok", "x"),
            doctor.CheckResult("b", "ok", "x"),
        ]
        assert doctor.exit_code(results) == 0

    def test_warn_only_returns_zero(self):
        results = [
            doctor.CheckResult("a", "ok", "x"),
            doctor.CheckResult("b", "warn", "x"),
        ]
        assert doctor.exit_code(results) == 0

    def test_any_fail_returns_one(self):
        results = [
            doctor.CheckResult("a", "ok", "x"),
            doctor.CheckResult("b", "fail", "x"),
        ]
        assert doctor.exit_code(results) == 1


class TestGlyph:
    def test_known_statuses(self):
        assert doctor.CheckResult("x", "ok", "").glyph == "[ok]"
        assert doctor.CheckResult("x", "warn", "").glyph == "[!!]"
        assert doctor.CheckResult("x", "fail", "").glyph == "[XX]"


# ---------------------------------------------------------------------------
# DB-backed checks
# ---------------------------------------------------------------------------


@pytest.fixture
async def factory(tmp_path):
    db_path = tmp_path / "doctor.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class TestDatabaseCheck:
    async def test_no_factory_fails(self):
        r = await doctor.check_database(None)
        assert r.status == "fail"

    async def test_reachable_factory_passes(self, factory):
        r = await doctor.check_database(factory)
        assert r.status == "ok"


class TestMCPServerCheck:
    async def test_active_integration_turns_missing_mcp_into_information(
        self, factory, monkeypatch
    ):
        import uuid

        from backend.db.models import Organization
        from backend.db.repos import IntegrationConnectorRepo

        org_id = uuid.uuid4()
        monkeypatch.setenv("OPSMENDER_SECRET_KEY", "doctor-test-secret")
        async with factory() as db:
            db.add(Organization(id=org_id, name="Doctor", slug="doctor"))
            await db.flush()
            await IntegrationConnectorRepo.create(
                db,
                org_id,
                kind="github",
                name="Native tools",
                base_url="https://example.test",
                auth_type="none",
                auth=None,
                config={},
                is_enabled=True,
            )
            await db.commit()

        results = await doctor.check_mcp_servers(factory)
        assert len(results) == 1
        assert results[0].status == "ok"
        assert results[0].name == "Infrastructure tools"
        assert "1 active integration connector" in results[0].detail


class TestPagingChainAge:
    async def test_empty_passes(self, factory):
        r = await doctor.check_paging_chain_age(factory)
        assert r.status == "ok"

    async def test_stale_chain_warns(self, factory):
        import uuid as uuidlib

        from backend.db.models import IncidentChainState, Organization

        org_id = uuidlib.uuid4()
        async with factory() as session:
            session.add(Organization(id=org_id, name="Test", slug="test"))
            # SQLite without PRAGMA foreign_keys=ON doesn't enforce the
            # FK, so we can plug in synthetic chain/incident UUIDs. The
            # check only reads IncidentChainState rows directly.
            session.add(
                IncidentChainState(
                    org_id=org_id,
                    incident_id=uuidlib.uuid4(),
                    chain_id=uuidlib.uuid4(),
                    status="running",
                    started_at=datetime.now(timezone.utc) - timedelta(hours=48),
                )
            )
            await session.commit()

        r = await doctor.check_paging_chain_age(factory)
        assert r.status == "warn"
        assert "1 chain" in r.detail


class TestRunAllChecks:
    async def test_orchestrator_returns_results_in_order(
        self, tmp_path, factory, monkeypatch
    ):
        monkeypatch.delenv("OPSMENDER_DEPLOYMENT_MODE", raising=False)
        (tmp_path / "index.html").write_text("ok")
        cfg = _config(
            secret="x" * 40,
            static_dir=str(tmp_path),
            audit_path=str(tmp_path / "audit.jsonl"),
        )
        results = await doctor.run_all_checks(cfg, factory)
        names = [r.name for r in results]
        # First four are the deterministic pure + DB checks
        assert names[:4] == [
            "JWT secret",
            "Frontend static mount",
            "Audit log",
            "Database",
        ]
        # Then MCP-servers placeholder (no servers seeded) then paging chains
        assert any(n.startswith("MCP") for n in names[4:])
        assert names[-1] == "Paging chains"
