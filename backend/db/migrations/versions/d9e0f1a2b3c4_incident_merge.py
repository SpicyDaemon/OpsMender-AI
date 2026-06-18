"""Combine incidents — merged_into_incident_id (v1.2).

Adds a nullable self-referential FK so a secondary incident can be folded
into a surviving (primary) incident with status="merged" instead of being
deleted.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(
            sa.Column("merged_into_incident_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_incidents_merged_into_incident_id",
            "incidents",
            ["merged_into_incident_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_constraint(
            "fk_incidents_merged_into_incident_id", type_="foreignkey"
        )
        batch_op.drop_column("merged_into_incident_id")
