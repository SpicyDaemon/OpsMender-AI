"""Add bot_action_audit table.

Revision ID: 9a2b3c4d5e6f
Revises: 8f1a2c3d4e5f
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9a2b3c4d5e6f"
down_revision = "8f1a2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_action_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "connector_id",
            sa.Uuid(),
            sa.ForeignKey("bot_connectors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("chat_id", sa.String(length=120), nullable=True),
        sa.Column("command", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bot_action_audit_connector_id",
        "bot_action_audit",
        ["connector_id"],
    )
    op.create_index(
        "ix_bot_action_audit_created_at",
        "bot_action_audit",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_action_audit_created_at", table_name="bot_action_audit")
    op.drop_index("ix_bot_action_audit_connector_id", table_name="bot_action_audit")
    op.drop_table("bot_action_audit")
