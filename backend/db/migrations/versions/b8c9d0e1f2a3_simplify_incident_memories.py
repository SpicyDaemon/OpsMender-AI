"""Remove incident-memory review and hidden state.

Memories are immediately recallable after creation. Existing pending, rejected,
and hidden rows remain as ordinary memories; only the obsolete state columns
and indexes are removed.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incident_memories") as batch_op:
        batch_op.drop_index("ix_incident_memories_org_hidden")
        batch_op.drop_index("ix_incident_memories_org_review")
        batch_op.drop_constraint(
            "fk_incident_memories_reviewed_by_user",
            type_="foreignkey",
        )
        batch_op.drop_column("is_hidden")
        batch_op.drop_column("review_status")
        batch_op.drop_column("reviewed_by_user_id")
        batch_op.drop_column("reviewed_at")


def downgrade() -> None:
    with op.batch_alter_table("incident_memories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_hidden",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "review_status",
                sa.String(length=20),
                nullable=False,
                server_default="approved",
            )
        )
        batch_op.add_column(
            sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_incident_memories_reviewed_by_user",
            "users",
            ["reviewed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_incident_memories_org_hidden", ["org_id", "is_hidden"]
        )
        batch_op.create_index(
            "ix_incident_memories_org_review", ["org_id", "review_status"]
        )
