"""Add in_app_notifications table for the per-user notification center.

Backs the v1.2 notification bell: each row is a per-(org, user) record of a
lifecycle event (assignment, paging, approval, incident state change, AI
session, @mention, reliability, account). ``read_at`` drives the unread
badge. No data migration — the center starts empty and fills as events fire.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3mtta01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_in_app_notifications_user_created",
        "in_app_notifications",
        ["org_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_in_app_notifications_user_unread",
        "in_app_notifications",
        ["org_id", "user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_in_app_notifications_user_unread", table_name="in_app_notifications"
    )
    op.drop_index(
        "ix_in_app_notifications_user_created", table_name="in_app_notifications"
    )
    op.drop_table("in_app_notifications")
