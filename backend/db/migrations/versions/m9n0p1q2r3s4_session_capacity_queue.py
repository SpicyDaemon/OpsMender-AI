"""Add durable session queue and approval hold metadata.

Revision ID: m9n0p1q2r3s4
Revises: l8m9n0p1q2r3
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m9n0p1q2r3s4"
down_revision: Union[str, Sequence[str], None] = "l8m9n0p1q2r3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column("requested_model_config_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(sa.Column("queued_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("queue_expires_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("queue_reason", sa.String(length=100)))
        batch_op.add_column(
            sa.Column(
                "force_started",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("force_started_by", sa.Uuid()))
        batch_op.add_column(sa.Column("force_start_occupancy", sa.Integer()))
        batch_op.add_column(sa.Column("force_start_cap", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_sessions_requested_model_config_id_model_configs",
            "model_configs",
            ["requested_model_config_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_sessions_force_started_by_users",
            "users",
            ["force_started_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_sessions_queued_at", "sessions", ["queued_at"])
    op.create_index("ix_sessions_queue_expires_at", "sessions", ["queue_expires_at"])
    op.create_index(
        "ix_sessions_org_status_queued",
        "sessions",
        ["org_id", "status", "queued_at"],
    )

    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.add_column(
            sa.Column(
                "extension_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("extension_notified_at", sa.DateTime(timezone=True))
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.drop_column("extension_notified_at")
        batch_op.drop_column("extension_count")
    op.drop_index("ix_sessions_org_status_queued", table_name="sessions")
    op.drop_index("ix_sessions_queue_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_queued_at", table_name="sessions")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_sessions_force_started_by_users", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_sessions_requested_model_config_id_model_configs",
            type_="foreignkey",
        )
        batch_op.drop_column("force_start_cap")
        batch_op.drop_column("force_start_occupancy")
        batch_op.drop_column("force_started_by")
        batch_op.drop_column("force_started")
        batch_op.drop_column("queue_reason")
        batch_op.drop_column("queue_expires_at")
        batch_op.drop_column("queued_at")
        batch_op.drop_column("requested_model_config_id")
