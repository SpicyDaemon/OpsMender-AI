"""Add format column to webhook_triggers.

Revision ID: a7b9c1d2e3f4
Revises: f1a2b3c4d5e7
Create Date: 2026-04-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b9c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("webhook_triggers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "format",
                sa.String(length=20),
                nullable=False,
                server_default="generic",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("webhook_triggers") as batch_op:
        batch_op.drop_column("format")
