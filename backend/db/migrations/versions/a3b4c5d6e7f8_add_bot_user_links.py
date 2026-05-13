"""Add bot_user_links table.

Revision ID: a3b4c5d6e7f8
Revises: 9a2b3c4d5e6f
Create Date: 2026-05-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a3b4c5d6e7f8"
down_revision = "9a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_user_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "connector_id",
            sa.Uuid(),
            sa.ForeignKey("bot_connectors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform_user_id", sa.String(length=120), nullable=False),
        sa.Column(
            "opsmender_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connector_id",
            "platform_user_id",
            name="uq_bot_user_links_connector_platform_user",
        ),
    )
    op.create_index(
        "ix_bot_user_links_opsmender_user_id",
        "bot_user_links",
        ["opsmender_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_user_links_opsmender_user_id", table_name="bot_user_links")
    op.drop_table("bot_user_links")
