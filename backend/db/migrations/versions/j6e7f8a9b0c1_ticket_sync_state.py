"""Add durable bi-directional ticket synchronization state.

Revision ID: j6e7f8a9b0c1
Revises: i5d6e7f8a9b0
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "i5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ticket_sync_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connector_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("external_ticket_id", sa.String(length=500), nullable=False),
        sa.Column("external_ticket_url", sa.String(length=2000), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_direction", sa.String(length=20), nullable=False),
        sa.Column("status_map", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["integration_connector_id"],
            ["integration_connectors.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_connector_id",
            "incident_id",
            name="uq_ticket_sync_connector_incident",
        ),
        sa.UniqueConstraint(
            "integration_connector_id",
            "external_ticket_id",
            name="uq_ticket_sync_connector_external",
        ),
    )
    op.create_index(
        "ix_ticket_sync_org_incident",
        "ticket_sync_state",
        ["org_id", "incident_id"],
        unique=False,
    )
    op.add_column(
        "incident_comments",
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            server_default="user",
        ),
    )


def downgrade() -> None:
    op.drop_column("incident_comments", "source")
    op.drop_index("ix_ticket_sync_org_incident", table_name="ticket_sync_state")
    op.drop_table("ticket_sync_state")
