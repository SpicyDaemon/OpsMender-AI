"""
Dev-mode backend launcher — no Postgres required.

Loads the shared env-based config, picks the same DB fallback chain
as the app, creates all tables, seeds an admin user, then starts
Uvicorn on port 8000.

Usage:
    uv run python scripts/dev_server.py

Default credentials:  admin / admin123
"""

import asyncio
import os
import sys
from dataclasses import replace

# Place project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _sqlite_literal(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _patch_sqlite_dev_schema(sync_conn, metadata) -> None:
    """Best-effort additive patching for stale local SQLite schemas.

    ``create_all()`` creates missing tables but does not add newly introduced
    columns to tables that already exist. Local dev databases can therefore
    drift behind the current ORM after sprint-to-sprint schema growth.
    For SQLite dev only, we add missing nullable/default-backed columns in
    place so older local DBs keep working without a manual reset.
    """

    from sqlalchemy import inspect

    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.name in existing_columns or column.primary_key:
                continue

            default_sql = None
            if column.default is not None and getattr(column.default, "is_scalar", False):
                default_sql = _sqlite_literal(column.default.arg)

            if not column.nullable and default_sql is None:
                print(
                    f"[dev] Skipping non-null column patch for {table.name}.{column.name} "
                    "(no scalar default available)."
                )
                continue

            type_sql = column.type.compile(dialect=sync_conn.dialect)
            statement = (
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'
            )
            if default_sql is not None:
                statement = f"{statement} DEFAULT {default_sql}"
            if not column.nullable and default_sql is not None:
                statement = f"{statement} NOT NULL"

            sync_conn.exec_driver_sql(statement)
            existing_columns.add(column.name)
            print(f"[dev] Patched SQLite schema: added {table.name}.{column.name}")


async def bootstrap():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.config_loader import AppConfig
    from backend.db.models import Base, User
    from backend.db.engine import resolve_database_url
    from backend.db.repos import UserRepo

    config = AppConfig.load()
    db_url = resolve_database_url(config.db)
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if db_url.startswith("sqlite"):
            await conn.run_sync(lambda sync_conn: _patch_sqlite_dev_schema(sync_conn, Base.metadata))
        print("[dev] Tables created (or already exist).")

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed admin user if not present
    async with factory() as session:
        existing = await UserRepo.get_by_username(session, "admin")
        if existing is None:
            import bcrypt
            hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
            await UserRepo.create(
                session,
                username="admin",
                email="admin@localhost",
                password_hash=hashed,
                role="admin",
            )
            await session.commit()
            print("[dev] Seeded admin user: admin / admin123")
        else:
            print("[dev] Admin user already exists.")

    resolved_config = replace(config, db=replace(config.db, url=db_url))
    return resolved_config


if __name__ == "__main__":
    config = asyncio.run(bootstrap())

    import uvicorn
    from backend.api.app import create_app

    print("[dev] Starting backend on http://localhost:8000")
    print("[dev] API docs at  http://localhost:8000/docs")
    uvicorn.run(create_app(config), host="0.0.0.0", port=8000, reload=False)
