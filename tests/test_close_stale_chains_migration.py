"""M1-106: the migration closes chains left running before the acknowledgement
lock, touches nothing else, and its downgrade leaves the data as it is.

Before D-021 a chain whose last level had fired kept status ``running`` with
no next due time and no finished time; the engine never ticks such a row and
``chain_is_live`` counts it as live. The migration marks them exhausted,
finished at the migration time, sends no notice (so ``exhaustion_notified_at``
stays empty, unlike every chain the engine exhausts) and the downgrade puts
them back. Both dialects run the same statements: SQLite here, PostgreSQL
under the integration marker with ``PART4_PG_URL``.
"""

from __future__ import annotations

import importlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATION = "backend.db.migrations.versions.f5a6b7c8d9e0_close_stale_running_chains"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

STALE = uuid.UUID("00000000-0000-0000-0000-000000000001")
LIVE = uuid.UUID("00000000-0000-0000-0000-000000000002")
EXHAUSTED = uuid.UUID("00000000-0000-0000-0000-000000000003")
ACKED = uuid.UUID("00000000-0000-0000-0000-000000000004")
NEW_RUN = uuid.UUID("00000000-0000-0000-0000-000000000005")
OLD_EXHAUSTED = uuid.UUID("00000000-0000-0000-0000-000000000006")


def _schema() -> tuple[sa.MetaData, sa.Table]:
    metadata = sa.MetaData()
    table = sa.Table(
        "incident_chain_states",
        metadata,
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("next_step_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhaustion_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    return metadata, table


def _rows() -> list[dict]:
    """Every row names every column: an executemany takes its parameter
    names from the first row and would drop the later rows' due times."""
    empty = {
        "next_step_due_at": None,
        "finished_at": None,
        "exhaustion_notified_at": None,
    }
    return [{**empty, **row} for row in _shapes()]


def _shapes() -> list[dict]:
    return [
        # The row this migration is for: running, nothing due, never finished.
        {"id": STALE, "status": "running"},
        # A live chain waiting for its next level.
        {"id": LIVE, "status": "running", "next_step_due_at": T0 + timedelta(hours=1)},
        # A chain the engine exhausted: it carries the notice stamp.
        {
            "id": EXHAUSTED,
            "status": "exhausted",
            "finished_at": T0,
            "exhaustion_notified_at": T0,
        },
        # An acknowledged chain under the lock, with its inactivity deadline.
        {"id": ACKED, "status": "acked", "next_step_due_at": T0 + timedelta(days=1)},
        # A chain acknowledged before the lock existed: finished, stays so.
        {"id": NEW_RUN, "status": "acked", "finished_at": T0},
        # A chain a release before 1.1.1 exhausted: finished, no notice stamp
        # (the column came later). It looks like a closed stale row, so only a
        # downgrade that leaves the data alone can keep it finished.
        {"id": OLD_EXHAUSTED, "status": "exhausted", "finished_at": T0},
    ]


def _state(connection, table) -> dict[uuid.UUID, tuple]:
    rows = connection.execute(
        sa.select(
            table.c.id,
            table.c.status,
            table.c.next_step_due_at,
            table.c.finished_at,
            table.c.exhaustion_notified_at,
        )
    ).all()
    return {row[0]: tuple(row[1:]) for row in rows}


def _exercise(connection, monkeypatch) -> None:
    """Upgrade, check, downgrade, check, upgrade again; on one connection."""
    migration = importlib.import_module(MIGRATION)
    metadata, table = _schema()
    metadata.drop_all(connection)
    metadata.create_all(connection)
    connection.execute(table.insert(), _rows())
    before = _state(connection, table)
    monkeypatch.setattr(
        migration, "op", Operations(MigrationContext.configure(connection))
    )

    migration.upgrade()
    after = _state(connection, table)
    status, due, finished, notified = after[STALE]
    assert status == "exhausted" and due is None and notified is None
    assert finished is not None
    assert finished.replace(tzinfo=timezone.utc) >= T0
    # Every other row is untouched.
    for key in (LIVE, EXHAUSTED, ACKED, NEW_RUN, OLD_EXHAUSTED):
        assert after[key] == before[key], key

    # The downgrade changes no row: nothing is revived as running.
    migration.downgrade()
    assert _state(connection, table) == after

    # A second upgrade finds nothing left to close.
    migration.upgrade()
    assert _state(connection, table) == after
    metadata.drop_all(connection)


def test_stale_running_chains_are_closed_and_restored_on_sqlite(monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _exercise(connection, monkeypatch)
    engine.dispose()


@pytest.mark.integration
async def test_stale_running_chains_are_closed_and_restored_on_postgresql(
    monkeypatch,
):
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_exercise, monkeypatch)
    finally:
        await engine.dispose()
