"""The environment scan audits are gone (M1-93, owner decision O-27).

The feature guessed from one read-only snapshot and failed on every run, so it
was removed: the auditor package, the /audits routes, the audit scheduler, the
three audit tables and the CLI command. The model settings helper that the
Skill Studio assist and the ingest extractor share moved to
backend/llm/provider_settings.py. The audit log of tool calls and admin changes
is a different feature and stays.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.config_loader import set_env_path
from backend.db.models import Base

AUDIT_TABLES = {"audit_runs", "audit_findings", "audit_schedules"}
MIGRATION = "backend.db.migrations.versions.e4f5a6b7c8d9_drop_environment_scan_audits"


@pytest.fixture
async def client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'removed.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    env = tmp_path / ".env"
    env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(env)
    app = create_app()
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield app, c
    set_env_path(None)
    await engine.dispose()


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/audits/analyzers"),
        ("GET", "/audits/runs"),
        ("POST", "/audits/runs"),
        ("GET", "/audits/findings"),
        ("GET", "/audits/schedules"),
        ("POST", "/audits/schedules"),
    ],
)
async def test_the_audit_routes_are_gone(client, method, path):
    """A removed path answers exactly like a path that never existed: 404, or
    405 for a POST while the web frontend's catch-all GET route is mounted."""
    app, c = client
    assert not any(
        getattr(route, "path", "").startswith("/audits") for route in app.routes
    )
    never_existed = await c.request(method, "/no-such-feature/items", json={})
    response = await c.request(method, path, json={})
    assert response.status_code == never_existed.status_code, (method, path)
    assert response.status_code in (404, 405)


def test_the_models_hold_no_audit_tables_but_keep_the_audit_log():
    tables = set(Base.metadata.tables)
    assert not tables & AUDIT_TABLES, sorted(tables & AUDIT_TABLES)
    assert "audit_entries" in tables  # the tool-call and admin-change audit log stays


def test_the_auditor_package_is_gone():
    assert importlib.util.find_spec("backend.auditor") is None


async def test_skill_assist_still_resolves_the_default_model(monkeypatch):
    """The helper moved out of the auditor package; the Skill Studio assist
    must still build its provider from the workspace's default model."""
    from backend.api.routes import skills
    from backend.llm import factory
    from backend.llm.providers import StubProvider

    model_cfg = SimpleNamespace(
        provider="lmstudio",
        model_id="local-model",
        api_key_env_var=None,
        base_url="http://127.0.0.1:1234/v1",
        api_version=None,
        provider_meta=None,
        max_tokens=512,
    )
    seen: dict = {}

    async def default_model(db, org_id):
        return model_cfg

    def create_provider(**kwargs):
        seen.update(kwargs)
        return StubProvider(response="classified")

    monkeypatch.setattr(skills.ModelConfigRepo, "get_default", default_model)
    monkeypatch.setattr(factory, "create_provider", create_provider)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None)))

    reply = await skills._ai_complete(
        request, None, uuid.uuid4(), "classify these tools"
    )

    assert reply == "classified"
    assert (
        seen["provider"] == "lmstudio"
        and seen["base_url"] == "http://127.0.0.1:1234/v1"
    )


def test_the_ingest_extractor_still_resolves_model_settings():
    from backend.ingest.llm_extractor import _resolve_model_kwargs

    model_cfg = SimpleNamespace(
        provider="ollama",
        model_id="llama",
        api_key_env_var=None,
        base_url="http://127.0.0.1:11434",
        api_version=None,
        provider_meta=None,
        max_tokens=256,
    )
    kwargs = _resolve_model_kwargs(None, model_cfg)
    assert (
        kwargs["provider"] == "ollama"
        and kwargs["base_url"] == "http://127.0.0.1:11434"
    )


def test_the_drop_migration_removes_the_tables_and_its_downgrade_recreates_them(
    monkeypatch,
):
    migration = importlib.import_module(MIGRATION)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    organizations = sa.Table(
        "organizations",
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
    )
    sa.Table("users", metadata, sa.Column("id", sa.Uuid, primary_key=True))
    sa.Table("sessions", metadata, sa.Column("id", sa.Uuid, primary_key=True))
    metadata.create_all(engine)
    org_id = uuid.uuid4()
    stamp = datetime(2026, 10, 25, tzinfo=timezone.utc)

    with engine.begin() as connection:
        connection.execute(organizations.insert().values(id=org_id, name="kept"))
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        # The revision before this one: the three tables exist.
        migration.downgrade()
        before = {
            t: {c["name"] for c in sa.inspect(connection).get_columns(t)}
            for t in AUDIT_TABLES
        }
        connection.execute(
            sa.text(
                "INSERT INTO audit_schedules (id, org_id, name, interval_minutes, next_run_at) "
                "VALUES (:id, :org, 'nightly', 60, :at)"
            ),
            {"id": uuid.uuid4().hex, "org": org_id.hex, "at": stamp},
        )

        migration.upgrade()
        assert not set(sa.inspect(connection).get_table_names()) & AUDIT_TABLES
        assert (
            connection.execute(sa.select(organizations.c.name)).scalar_one() == "kept"
        )

        migration.downgrade()
        after = {
            t: {c["name"] for c in sa.inspect(connection).get_columns(t)}
            for t in AUDIT_TABLES
        }
        assert after == before
        assert "claimed_at" in after["audit_schedules"]
        assert connection.scalar(sa.text("SELECT count(*) FROM audit_schedules")) == 0
    engine.dispose()
