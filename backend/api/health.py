"""Platform-neutral process liveness and database readiness checks."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text


@lru_cache(maxsize=1)
def migration_head() -> str:
    """Return the single migration head shipped with this process."""
    config = Config()
    migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
    config.set_main_option("script_location", str(migrations))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected one Alembic head, found {len(heads)}")
    return heads[0]


def _current_revision(connection: Any) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def readiness_status(app: Any) -> tuple[dict[str, str], int]:
    """Return the readiness response body and HTTP status for *app*."""
    if not getattr(app.state, "startup_complete", False):
        return (
            {
                "status": "not_ready",
                "database": "unknown",
                "migrations": "unknown",
                "reason": "startup_incomplete",
            },
            503,
        )

    engine = getattr(app.state, "engine", None)
    if engine is None:
        return (
            {
                "status": "not_ready",
                "database": "error",
                "migrations": "unknown",
                "reason": "database_unavailable",
            },
            503,
        )

    check_migrations = getattr(app.state, "service_role", "all") in {"all", "api"}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            if check_migrations:
                current_revision = await connection.run_sync(_current_revision)
            else:
                current_revision = None
    except Exception:  # noqa: BLE001 - readiness must convert failures to 503
        return (
            {
                "status": "not_ready",
                "database": "error",
                "migrations": "unknown",
                "reason": "database_unavailable",
            },
            503,
        )

    if not check_migrations:
        return {"status": "ready", "database": "ok", "migrations": "skipped"}, 200

    try:
        expected_revision = migration_head()
    except Exception:  # noqa: BLE001 - a broken package is not ready
        return (
            {
                "status": "not_ready",
                "database": "ok",
                "migrations": "unknown",
                "reason": "migration_head_unavailable",
            },
            503,
        )

    if current_revision != expected_revision:
        return (
            {
                "status": "not_ready",
                "database": "ok",
                "migrations": "behind" if current_revision else "unknown",
                "reason": "migration_revision_mismatch",
            },
            503,
        )

    return {"status": "ready", "database": "ok", "migrations": "current"}, 200
