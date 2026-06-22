"""Add pinned flag to incident memories (bounded-growth protection).

Revision ID: o1p2q3r4s5t6
Revises: n0p1q2r3s4t5
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o1p2q3r4s5t6"
down_revision: Union[str, Sequence[str], None] = "n0p1q2r3s4t5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("incident_memories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("incident_memories") as batch_op:
        batch_op.drop_column("pinned")
