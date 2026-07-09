"""Rename service preferred models to models.

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
"""

from alembic import op
import sqlalchemy as sa


revision = "k0l1m2n3o4p5"
down_revision = "j9k0l1m2n3o4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.alter_column(
            "preferred_model_config_ids",
            new_column_name="model_config_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.alter_column(
            "model_config_ids",
            new_column_name="preferred_model_config_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
