"""add mcp_servers table

Revision ID: 4f3b6c7d8e91
Revises: 9c2f1e4b6a11
Create Date: 2026-04-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "4f3b6c7d8e91"
down_revision = "9c2f1e4b6a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("command", sa.String(length=500), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("env_vars", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("mcp_servers")
