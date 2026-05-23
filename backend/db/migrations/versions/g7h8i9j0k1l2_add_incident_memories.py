"""Add incident_memories + incident_memory_recall_log (Sprint 45).

Revision ID: g7h8i9j0k1l2
Revises: f6b7c8d9e0f1
Create Date: 2026-05-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "f6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incident_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Uuid(),
            sa.ForeignKey("services.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary_md", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column(
            "helpful_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "unhelpful_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_hidden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_by_user_id",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_incident_memories_org_service",
        "incident_memories",
        ["org_id", "service_id"],
    )
    op.create_index(
        "ix_incident_memories_org_hidden",
        "incident_memories",
        ["org_id", "is_hidden"],
    )

    op.create_table(
        "incident_memory_recall_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "memory_id",
            sa.Uuid(),
            sa.ForeignKey("incident_memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "surfaced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("score", sa.Numeric(6, 3), nullable=True),
    )
    op.create_index(
        "ix_incident_memory_recall_session",
        "incident_memory_recall_log",
        ["session_id", "surfaced_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_memory_recall_session",
        table_name="incident_memory_recall_log",
    )
    op.drop_table("incident_memory_recall_log")
    op.drop_index(
        "ix_incident_memories_org_hidden", table_name="incident_memories"
    )
    op.drop_index(
        "ix_incident_memories_org_service", table_name="incident_memories"
    )
    op.drop_table("incident_memories")
