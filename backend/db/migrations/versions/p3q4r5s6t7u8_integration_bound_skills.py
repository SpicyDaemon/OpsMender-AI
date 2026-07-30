"""Bind saved skills to integration connectors.

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p3q4r5s6t7u8"
down_revision = "o2p3q4r5s6t7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.add_column(
            sa.Column("integration_connector_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_skills_integration_connector_id",
            "integration_connectors",
            ["integration_connector_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_skills_integration_connector_id",
            ["integration_connector_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_index("ix_skills_integration_connector_id")
        batch_op.drop_constraint(
            "fk_skills_integration_connector_id", type_="foreignkey"
        )
        batch_op.drop_column("integration_connector_id")
