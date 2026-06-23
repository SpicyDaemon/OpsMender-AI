"""Add manual queue_rank override for admin queue reordering.

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p2q3r4s5t6u7"
down_revision: Union[str, Sequence[str], None] = "o1p2q3r4s5t6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("queue_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("queue_rank")
