"""Incident first-acknowledgment timestamp (MTTA).

Adds ``incidents.acknowledged_at`` to power MTTA (median time from
``created_at`` to first acknowledgment). The column is stamped once, the first
time an incident gains an assignee (self-ack / chain-ack / takeover).

Existing incidents are backfilled from the earliest ``incident_assignments``
row per incident, so historical MTTA is available without re-acking.

Revision ID: a1b2c3mtta01
Revises: e0f1a2b3c4d5
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3mtta01"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True)
        )
    # Backfill from the earliest assignment per incident. Any assignment counts
    # as the first human acknowledgment (matches the going-forward write path).
    # Correlated subquery so this runs identically on SQLite and Postgres.
    op.execute(
        """
        UPDATE incidents
        SET acknowledged_at = (
            SELECT MIN(assigned_at)
            FROM incident_assignments
            WHERE incident_assignments.incident_id = incidents.id
        )
        WHERE acknowledged_at IS NULL
          AND EXISTS (
            SELECT 1
            FROM incident_assignments
            WHERE incident_assignments.incident_id = incidents.id
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_column("acknowledged_at")
