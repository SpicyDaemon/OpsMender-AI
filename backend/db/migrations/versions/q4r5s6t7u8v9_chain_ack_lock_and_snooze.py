"""Acknowledgement lock and snooze timing on incident chain states.

Adds ``paused_until`` (a timed snooze) and ``last_activity_at`` (the
assignee's last recorded write) for the D-021 acknowledgement lock. Existing
rows are left as they are: a chain acknowledged before this revision keeps its
``finished_at`` and stays finished, and a chain snoozed before it has no
``paused_until``, so the upgrade never re-pages historical incidents.

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q4r5s6t7u8v9"
down_revision = "p3q4r5s6t7u8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incident_chain_states") as batch_op:
        batch_op.add_column(
            sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("incident_chain_states") as batch_op:
        batch_op.drop_column("last_activity_at")
        batch_op.drop_column("paused_until")
