"""Rename service preferred MCP servers to MCP servers.

Revision ID: l0m1n2o3p4q5
Revises: k0l1m2n3o4p5
"""

from alembic import op
import sqlalchemy as sa


revision = "l0m1n2o3p4q5"
down_revision = "k0l1m2n3o4p5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.alter_column(
            "preferred_mcp_server_ids",
            new_column_name="mcp_server_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.alter_column(
            "mcp_server_ids",
            new_column_name="preferred_mcp_server_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
