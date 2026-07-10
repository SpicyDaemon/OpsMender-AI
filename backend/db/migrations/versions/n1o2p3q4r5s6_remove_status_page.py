"""Remove the workspace Status Page feature.

Drops the status_page_* tables and the Organization status_page_* columns. The
``audit_entries.session_id`` column stays nullable (the Status Page migration
relaxed it, but voice-settings / API-token / other non-session audit entries now
depend on that), so this migration does not revert it.

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n1o2p3q4r5s6"
down_revision = "m0n1o2p3q4r5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("status_page_subscribers")
    op.drop_table("status_page_updates")
    op.drop_table("status_page_components")
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("status_page_description")
        batch_op.drop_column("status_page_title")
        batch_op.drop_column("status_page_visibility")
        batch_op.drop_column("status_page_enabled")


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status_page_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "status_page_visibility",
                sa.String(length=10),
                nullable=False,
                server_default="private",
            )
        )
        batch_op.add_column(
            sa.Column("status_page_title", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("status_page_description", sa.Text(), nullable=True)
        )

    op.create_table(
        "status_page_components",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=150), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "service_id", name="uq_status_page_component_service"
        ),
    )
    op.create_index(
        "ix_status_page_components_org_id", "status_page_components", ["org_id"]
    )
    op.create_index(
        "ix_status_page_components_service_id",
        "status_page_components",
        ["service_id"],
    )

    op.create_table(
        "status_page_updates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_status_page_updates_org_id", "status_page_updates", ["org_id"]
    )
    op.create_index(
        "ix_status_page_updates_incident_id",
        "status_page_updates",
        ["incident_id"],
    )
    op.create_index(
        "ix_status_page_updates_org_published",
        "status_page_updates",
        ["org_id", "published_at"],
    )

    op.create_table(
        "status_page_subscribers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("confirm_token_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribe_token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "email", name="uq_status_page_subscriber_email"
        ),
    )
    op.create_index(
        "ix_status_page_subscribers_org_id", "status_page_subscribers", ["org_id"]
    )
