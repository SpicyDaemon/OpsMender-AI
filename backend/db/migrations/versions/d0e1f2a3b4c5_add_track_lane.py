"""Add Track-lane channel state.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_connectors",
        sa.Column("lanes", sa.JSON(), nullable=True),
    )

    connection = op.get_bind()
    connectors = sa.table(
        "bot_connectors",
        sa.column("id", sa.Uuid()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("allowed_capabilities", sa.JSON()),
        sa.column("lanes", sa.JSON()),
    )
    for row in connection.execute(
        sa.select(
            connectors.c.id,
            connectors.c.is_enabled,
            connectors.c.allowed_capabilities,
        )
    ):
        raw = row.allowed_capabilities
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        lanes = ["respond"] if row.is_enabled and "notifications" in (raw or []) else []
        connection.execute(
            connectors.update()
            .where(connectors.c.id == row.id)
            .values(lanes=lanes)
        )

    with op.batch_alter_table("bot_connectors") as batch_op:
        batch_op.alter_column(
            "lanes",
            existing_type=sa.JSON(),
            nullable=False,
        )

    op.create_table(
        "incident_track_posts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("external_message_id", sa.String(length=200), nullable=True),
        sa.Column("channel_ref", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["bot_connectors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_id",
            "connector_id",
            name="uq_incident_track_posts_incident_connector",
        ),
    )
    op.create_index(
        "ix_incident_track_posts_org_incident",
        "incident_track_posts",
        ["org_id", "incident_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_track_posts_org_incident",
        table_name="incident_track_posts",
    )
    op.drop_table("incident_track_posts")
    op.drop_column("bot_connectors", "lanes")
