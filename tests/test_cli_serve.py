from __future__ import annotations

import asyncio
import logging
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from backend.api.health import migration_head, readiness_status
from backend.db.engine import get_engine
from cli.opsmender import _parse_args, _run_serve


def _serve_args(*, skip_migrations: bool = False) -> Namespace:
    return Namespace(
        config=None,
        host="127.0.0.1",
        port=8765,
        reload=False,
        skip_migrations=skip_migrations,
    )


def test_no_migrate_alias_uses_existing_skip_flag():
    args = _parse_args(["serve", "--no-migrate"])
    assert args.skip_migrations is True


def test_sqlite_serve_creates_schema_stamps_head_and_is_ready(
    tmp_path,
    monkeypatch,
    capsys,
    caplog,
):
    db_path = tmp_path / "evaluation.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
    uvicorn_run = Mock()
    monkeypatch.setattr("uvicorn.run", uvicorn_run)
    caplog.set_level(logging.WARNING, logger="tests.cli_serve_logging")

    assert _run_serve(_serve_args()) == 0
    logging.getLogger("tests.cli_serve_logging").warning("capture survives stamp")

    sync_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with sync_engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            current_revision = MigrationContext.configure(
                connection
            ).get_current_revision()
    finally:
        sync_engine.dispose()

    assert "users" in tables
    assert "organizations" in tables
    assert current_revision == migration_head()
    assert "SQLite is for local evaluation only" in capsys.readouterr().out
    assert "capture survives stamp" in caplog.text
    assert uvicorn_run.call_args.kwargs == {
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "reload": False,
    }

    async def check_readiness():
        engine = get_engine(database_url)
        app = SimpleNamespace(
            state=SimpleNamespace(
                startup_complete=True,
                engine=engine,
                service_role="all",
            )
        )
        try:
            return await readiness_status(app)
        finally:
            await engine.dispose()

    body, status_code = asyncio.run(check_readiness())
    assert status_code == 200
    assert body == {
        "status": "ready",
        "database": "ok",
        "migrations": "current",
    }


def test_sqlite_serve_respects_skip_migrations(tmp_path, monkeypatch):
    db_path = tmp_path / "untouched.db"
    monkeypatch.setenv(
        "OPSMENDER_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
    )
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
    monkeypatch.setattr("uvicorn.run", Mock())

    assert _run_serve(_serve_args(skip_migrations=True)) == 0
    assert not db_path.exists()


def test_postgres_serve_keeps_alembic_upgrade_path(monkeypatch):
    monkeypatch.setenv(
        "OPSMENDER_DATABASE_URL",
        "postgresql+asyncpg://opsmender:secret@database/opsmender",
    )
    upgrade = Mock()
    stamp = Mock()
    monkeypatch.setattr("alembic.command.upgrade", upgrade)
    monkeypatch.setattr("alembic.command.stamp", stamp)
    monkeypatch.setattr("uvicorn.run", Mock())

    assert _run_serve(_serve_args()) == 0
    upgrade.assert_called_once()
    assert upgrade.call_args.args[1] == "head"
    stamp.assert_not_called()
