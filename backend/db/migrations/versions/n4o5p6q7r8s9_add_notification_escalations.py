"""Add notification_escalations table for staged per-priority routing.

Backs the staged notification escalation engine: each priority's ordered
routing stages fire over time (with delays) until the incident is
acknowledged or resolved. Existing single/legacy routing keeps working with
no data migration — legacy entries are read as Stage 1.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, Sequence[str], None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_escalations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=True),
        sa.Column("stages", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="running"
        ),
        sa.Column("current_stage", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("next_stage_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_id", "user_id", name="uq_notification_escalation_incident_user"
        ),
    )
    op.create_index(
        "ix_notification_escalations_due",
        "notification_escalations",
        ["org_id", "status", "next_stage_due_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_escalations_due", table_name="notification_escalations"
    )
    op.drop_table("notification_escalations")
