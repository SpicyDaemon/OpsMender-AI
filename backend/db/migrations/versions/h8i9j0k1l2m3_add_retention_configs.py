"""Add retention_configs (Sprint 53).

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-05-24 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, Sequence[str], None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retention_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("ttl_days", sa.Integer(), nullable=True),
        sa.Column(
            "updated_by_user_id",
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
        sa.Column("last_pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pruned_count", sa.Integer(), nullable=True),
        sa.UniqueConstraint("org_id", "category", name="uq_retention_org_category"),
    )


def downgrade() -> None:
    op.drop_table("retention_configs")
