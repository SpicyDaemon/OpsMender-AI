"""Add service-specific AI tier and auto-start policy.

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
"""

from alembic import op
import sqlalchemy as sa


revision = "z5a6b7c8d9e0"
down_revision = "y4z5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column("ai_default_tier", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ai_auto_start_enabled", sa.Boolean(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_services_ai_default_tier",
            "ai_default_tier IS NULL OR ai_default_tier IN (0, 1, 2)",
        )


def downgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_constraint("ck_services_ai_default_tier", type_="check")
        batch_op.drop_column("ai_auto_start_enabled")
        batch_op.drop_column("ai_default_tier")
