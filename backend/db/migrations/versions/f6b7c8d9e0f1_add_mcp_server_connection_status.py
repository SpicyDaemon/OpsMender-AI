"""Add runtime connection status columns to mcp_servers.

Revision ID: f6b7c8d9e0f1
Revises: f1a2b3c4d5e6
Create Date: 2026-05-20 09:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("last_successful_call_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "last_error")
    op.drop_column("mcp_servers", "last_successful_call_at")
