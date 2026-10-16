"""Scheduler claims: audit_schedules.claimed_at and sla_targets.poll_claimed_at (M1-68).

A due audit schedule and an SLA target's poll are claimed per row before they
run, so several app processes share them without running a job twice. Both
columns are empty for every existing row; nothing else changes.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_schedules") as batch_op:
        batch_op.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
    with op.batch_alter_table("sla_targets") as batch_op:
        batch_op.add_column(
            sa.Column("poll_claimed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("sla_targets") as batch_op:
        batch_op.drop_column("poll_claimed_at")
    with op.batch_alter_table("audit_schedules") as batch_op:
        batch_op.drop_column("claimed_at")
