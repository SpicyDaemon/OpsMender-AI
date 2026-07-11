"""Shared database bootstrap helpers for local SQLite evaluation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.db.models import Base

Reporter = Callable[[str], None]


def _sqlite_literal(value: Any) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def patch_sqlite_schema(
    sync_conn,
    metadata=Base.metadata,
    *,
    reporter: Reporter | None = None,
) -> None:
    """Add nullable/default-backed ORM columns missing from a local SQLite DB."""

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
            if column.default is not None and getattr(
                column.default, "is_scalar", False
            ):
                default_sql = _sqlite_literal(column.default.arg)

            if not column.nullable and default_sql is None:
                if reporter is not None:
                    reporter(
                        f"Skipping non-null column patch for "
                        f"{table.name}.{column.name} (no scalar default available)."
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
            if reporter is not None:
                reporter(f"Patched SQLite schema: added {table.name}.{column.name}")


async def initialize_sqlite_schema(
    engine: AsyncEngine,
    *,
    reporter: Reporter | None = None,
) -> None:
    """Create the ORM schema and repair additive drift on a SQLite engine."""

    if engine.url.get_backend_name() != "sqlite":
        raise ValueError("initialize_sqlite_schema requires a SQLite engine")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(
            lambda sync_conn: patch_sqlite_schema(
                sync_conn,
                Base.metadata,
                reporter=reporter,
            )
        )
