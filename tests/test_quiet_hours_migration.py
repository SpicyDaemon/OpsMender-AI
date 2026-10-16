"""M1-59: the data migration that stores Inbox quiet hours apart from My
Routing's page quiet hours, run on SQLite."""

from __future__ import annotations

import importlib
import uuid

import sqlalchemy as sa

INBOX = {"enabled": True, "start": "22:00", "end": "07:00", "tz": "Europe/Berlin"}
PAGE = {
    "weekday_start": "22:00",
    "weekday_end": "07:00",
    "days": [0, 1, 2, 3, 4],
    "min_priority_to_break": "P0",
    "time_zone": "UTC",
}


def test_upgrade_moves_inbox_quiet_hours_and_downgrade_moves_them_back(monkeypatch):
    migration = importlib.import_module(
        "backend.db.migrations.versions.6d2f9a41c8b3_inbox_quiet_hours_apart"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    prefs = sa.Table(
        "user_notification_prefs",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("routing", sa.JSON(), nullable=False),
        sa.Column("quiet_hours", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)

    inbox, bare, page, both, empty = (uuid.uuid4() for _ in range(5))
    off = {**INBOX, "enabled": False}
    before = {
        # Saved by the Inbox page, next to its mutes.
        inbox: ({"P1": ["email"], "in_app": {"muted_categories": ["incident"]}}, INBOX),
        # Saved by the Inbox page before any mute.
        bare: ({}, off),
        # My Routing's page quiet hours stay in the column.
        page: ({"P0": [{"channel_id": "email", "delay_seconds": 0}]}, PAGE),
        # Both settings already apart: the column is not empty on downgrade.
        both: ({"in_app": {"quiet_hours": INBOX}}, PAGE),
        empty: ({}, None),
    }

    def read(connection):
        query = sa.select(
            prefs.c.id,
            prefs.c.routing,
            prefs.c.quiet_hours,
            prefs.c.quiet_hours.is_(None).label("is_null"),
        )
        rows = connection.execute(query).all()
        saved = {row.id: (row.routing, row.quiet_hours) for row in rows}
        return saved, {row.id for row in rows if row.is_null}

    with engine.begin() as connection:
        connection.execute(
            prefs.insert(),
            [
                {"id": key, "routing": routing, "quiet_hours": quiet_hours}
                for key, (routing, quiet_hours) in before.items()
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()
        upgraded, cleared = read(connection)
        migration.downgrade()
        downgraded, _ = read(connection)

    assert upgraded == {
        inbox: (
            {
                "P1": ["email"],
                "in_app": {"muted_categories": ["incident"], "quiet_hours": INBOX},
            },
            None,
        ),
        bare: ({"in_app": {"quiet_hours": off}}, None),
        page: before[page],
        both: before[both],
        empty: before[empty],
    }
    # The moved rows hold SQL NULL in the column, not a JSON null.
    assert cleared == {inbox, bare}
    assert downgraded == before
