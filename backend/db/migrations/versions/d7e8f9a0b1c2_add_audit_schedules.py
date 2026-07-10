"""Add audit_schedules table (Sprint 39 step 2).

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-05-17 12:00:00.000000

Sprint 39 step 2 — scheduled audit runs. v1 uses a simple
``interval_minutes`` field (15-min minimum) rather than full cron
expressions to keep the dependency surface tight. The background
scheduler polls this table every minute and kicks off a queued
``audit_runs`` row whenever ``next_run_at`` has passed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("analyzers", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("mcp_server_name", sa.String(length=200), nullable=True),
        sa.Column("focus_areas", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "interval_minutes >= 15", name="ck_audit_schedules_interval_min"
        ),
    )
    op.create_index(
        "ix_audit_schedules_due",
        "audit_schedules",
        ["is_active", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_schedules_due", table_name="audit_schedules")
    op.drop_table("audit_schedules")
