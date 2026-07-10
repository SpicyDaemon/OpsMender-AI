"""add v1.2 incident-memory review gate

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
Create Date: 2026-06-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "x3y4z5a6b7c8"
down_revision = "w2x3y4z5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incident_memories",
        sa.Column(
            "review_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "incident_memories",
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "incident_memories",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_incident_memories_reviewed_by_user",
        "incident_memories",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_incident_memories_org_review",
        "incident_memories",
        ["org_id", "review_status"],
    )
    # Existing memories were already in use (advisory recall), so preserve that
    # behavior: mark them approved. New AI-written memories default to pending.
    op.execute(
        "UPDATE incident_memories SET review_status = 'approved' "
        "WHERE review_status = 'pending'"
    )


def downgrade() -> None:
    op.drop_index("ix_incident_memories_org_review", table_name="incident_memories")
    op.drop_constraint(
        "fk_incident_memories_reviewed_by_user",
        "incident_memories",
        type_="foreignkey",
    )
    op.drop_column("incident_memories", "reviewed_at")
    op.drop_column("incident_memories", "reviewed_by_user_id")
    op.drop_column("incident_memories", "review_status")
