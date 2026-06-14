"""add v1.1 notification callback foundation

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
Create Date: 2026-06-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "w2x3y4z5a6b7"
down_revision = "v1w2x3y4z5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_connectors",
        sa.Column(
            "native_actions_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "bot_connectors",
        sa.Column(
            "callback_status",
            sa.String(length=30),
            nullable=False,
            server_default="not_configured",
        ),
    )
    op.add_column(
        "bot_connectors",
        sa.Column(
            "callback_last_verified_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "bot_connectors",
        sa.Column("callback_last_error", sa.Text(), nullable=True),
    )

    op.add_column(
        "bot_user_links",
        sa.Column("external_username", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "bot_user_links",
        sa.Column("external_display_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "bot_user_links",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bot_user_links",
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.add_column(
        "bot_action_audit",
        sa.Column("incident_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "bot_action_audit",
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "bot_action_audit",
        sa.Column("external_user_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "bot_action_audit",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.create_foreign_key(
        "fk_bot_action_audit_incident_id",
        "bot_action_audit",
        "incidents",
        ["incident_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_bot_action_audit_actor_user_id",
        "bot_action_audit",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "incident_notification_receipts",
        sa.Column(
            "delivery_status",
            sa.String(length=30),
            nullable=False,
            server_default="delivered",
        ),
    )
    op.add_column(
        "incident_notification_receipts",
        sa.Column("update_failed_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "incident_notification_receipts",
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "incident_notification_receipts",
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "native_action_invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("external_user_id", sa.String(length=200), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("result_status", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("callback_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["bot_connectors.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "connector_id",
            "idempotency_key",
            name="uq_native_action_invocation_key",
        ),
    )
    op.create_index(
        "ix_native_action_invocations_incident",
        "native_action_invocations",
        ["org_id", "incident_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_native_action_invocations_incident",
        table_name="native_action_invocations",
    )
    op.drop_table("native_action_invocations")

    op.drop_column("incident_notification_receipts", "last_updated_at")
    op.drop_column("incident_notification_receipts", "last_sent_at")
    op.drop_column("incident_notification_receipts", "update_failed_reason")
    op.drop_column("incident_notification_receipts", "delivery_status")

    op.drop_constraint(
        "fk_bot_action_audit_actor_user_id",
        "bot_action_audit",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_bot_action_audit_incident_id",
        "bot_action_audit",
        type_="foreignkey",
    )
    op.drop_column("bot_action_audit", "idempotency_key")
    op.drop_column("bot_action_audit", "external_user_id")
    op.drop_column("bot_action_audit", "actor_user_id")
    op.drop_column("bot_action_audit", "incident_id")

    op.drop_column("bot_user_links", "verified")
    op.drop_column("bot_user_links", "last_seen_at")
    op.drop_column("bot_user_links", "external_display_name")
    op.drop_column("bot_user_links", "external_username")

    op.drop_column("bot_connectors", "callback_last_error")
    op.drop_column("bot_connectors", "callback_last_verified_at")
    op.drop_column("bot_connectors", "callback_status")
    op.drop_column("bot_connectors", "native_actions_enabled")
