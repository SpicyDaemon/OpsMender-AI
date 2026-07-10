"""add incident notification receipts

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "t9u0v1w2x3y4"
down_revision = "s8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_notification_receipts",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("external_channel_id", sa.String(length=200), nullable=True),
        sa.Column("external_message_id", sa.String(length=200), nullable=True),
        sa.Column("external_thread_id", sa.String(length=200), nullable=True),
        sa.Column("lifecycle_event", sa.String(length=80), nullable=False),
        sa.Column("rendered_status", sa.String(length=40), nullable=True),
        sa.Column("can_update", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["bot_connectors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_notification_receipts_incident_connector",
        "incident_notification_receipts",
        ["org_id", "incident_id", "connector_id", "external_channel_id"],
    )
    op.create_index(
        "ix_incident_notification_receipts_session",
        "incident_notification_receipts",
        ["org_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_notification_receipts_session",
        table_name="incident_notification_receipts",
    )
    op.drop_index(
        "ix_incident_notification_receipts_incident_connector",
        table_name="incident_notification_receipts",
    )
    op.drop_table("incident_notification_receipts")
