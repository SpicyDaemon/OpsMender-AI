"""Receipt upgrades preserve historical delivery times on SQLite."""

import importlib
from datetime import datetime

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_card_event_time_upgrade_preserves_legacy_receipt(monkeypatch):
    migration = importlib.import_module(
        "backend.db.migrations.versions.d3e4f5a6b7c8_card_event_time"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    receipts = sa.Table(
        "incident_notification_receipts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("external_message_id", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            receipts.insert().values(
                id=1,
                external_message_id="existing-card",
                created_at=datetime(2026, 10, 21),
            )
        )
        before = connection.execute(sa.select(receipts)).one()
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        assert connection.execute(sa.select(receipts)).one() == before
        assert (
            connection.scalar(
                sa.text("SELECT event_generated_at FROM incident_notification_receipts")
            )
            is None
        )
        migration.downgrade()
        assert connection.execute(sa.select(receipts)).one() == before
        migration.upgrade()
        assert connection.execute(sa.select(receipts)).one() == before
    engine.dispose()
