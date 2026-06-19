"""Add durable incident references for source-control integrations.

Revision ID: g3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_integration_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("reference_meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["integration_connectors.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_id",
            "connector_id",
            "reference_type",
            "external_id",
            name="uq_incident_integration_reference",
        ),
    )
    op.create_index(
        "ix_incident_integration_links_incident",
        "incident_integration_links",
        ["org_id", "incident_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_integration_links_incident",
        table_name="incident_integration_links",
    )
    op.drop_table("incident_integration_links")
