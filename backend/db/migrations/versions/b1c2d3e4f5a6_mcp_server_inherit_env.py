"""MCP servers: inherit_env, true for every server saved before v1.2 (M1-64).

A local MCP server now receives only PATH, the OS basics and its own
variables. Servers that exist before this migration keep receiving the whole
process environment until an admin saves them again, so nothing breaks on
upgrade; new servers do not inherit.

Revision ID: b1c2d3e4f5a6
Revises: 6d2f9a41c8b3
"""

from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "6d2f9a41c8b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "inherit_env", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    servers = sa.table("mcp_servers", sa.column("inherit_env", sa.Boolean()))
    op.get_bind().execute(servers.update().values(inherit_env=True))


def downgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.drop_column("inherit_env")
