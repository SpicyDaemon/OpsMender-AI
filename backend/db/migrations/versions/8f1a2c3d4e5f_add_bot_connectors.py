"""Add bot_connectors table.

Revision ID: 8f1a2c3d4e5f
Revises: bdfdddfc8de7
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "8f1a2c3d4e5f"
down_revision = "bdfdddfc8de7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_connectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("credentials", sa.JSON(), nullable=True),
        sa.Column("allowed_capabilities", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="not_configured",
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("bot_connectors")
