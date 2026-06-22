"""Add resumable session progress snapshot.

Revision ID: n0p1q2r3s4t5
Revises: m9n0p1q2r3s4
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n0p1q2r3s4t5"
down_revision: Union[str, Sequence[str], None] = "m9n0p1q2r3s4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("progress")
