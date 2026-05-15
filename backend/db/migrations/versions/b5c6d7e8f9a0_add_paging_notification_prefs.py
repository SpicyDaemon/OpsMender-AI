"""Add paging notification preferences.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-05-15 14:00:00.000000

Sprint 35 extends the older SLA maintenance-window table so it can also
scope paging suppression, and adds per-user notification preferences plus an
organization-level page dedup window.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "notification_dedup_window_minutes",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.alter_column(
        "organizations", "notification_dedup_window_minutes", server_default=None
    )

    op.add_column(
        "maintenance_windows", sa.Column("description", sa.Text(), nullable=True)
    )
    op.add_column(
        "maintenance_windows",
        sa.Column(
            "scope_type", sa.String(length=20), nullable=False, server_default="global"
        ),
    )
    op.alter_column("maintenance_windows", "scope_type", server_default=None)
    op.add_column(
        "maintenance_windows", sa.Column("scope_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        "ix_maintenance_windows_org_range",
        "maintenance_windows",
        ["org_id", "starts_at", "ends_at"],
    )
    op.create_index(
        "ix_maintenance_windows_scope",
        "maintenance_windows",
        ["org_id", "scope_type", "scope_id"],
    )

    op.add_column(
        "incidents",
        sa.Column("suppressed_by_maintenance_window_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_incidents_suppressed_by_maintenance_window_id",
        "incidents",
        "maintenance_windows",
        ["suppressed_by_maintenance_window_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "user_notification_prefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channels", postgresql.JSONB, nullable=False),
        sa.Column("routing", postgresql.JSONB, nullable=False),
        sa.Column("quiet_hours", postgresql.JSONB, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "org_id", name="uq_user_notification_pref"),
    )
    op.create_index(
        "ix_user_notification_prefs_org_user",
        "user_notification_prefs",
        ["org_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_notification_prefs_org_user", table_name="user_notification_prefs"
    )
    op.drop_table("user_notification_prefs")

    op.drop_constraint(
        "fk_incidents_suppressed_by_maintenance_window_id",
        "incidents",
        type_="foreignkey",
    )
    op.drop_column("incidents", "suppressed_by_maintenance_window_id")

    op.drop_index("ix_maintenance_windows_scope", table_name="maintenance_windows")
    op.drop_index("ix_maintenance_windows_org_range", table_name="maintenance_windows")
    op.drop_column("maintenance_windows", "scope_id")
    op.drop_column("maintenance_windows", "scope_type")
    op.drop_column("maintenance_windows", "description")
    op.drop_column("organizations", "notification_dedup_window_minutes")
