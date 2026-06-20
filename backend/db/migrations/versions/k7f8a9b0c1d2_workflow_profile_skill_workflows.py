"""Add Skill workflow toggle to session profiles.

Revision ID: k7f8a9b0c1d2
Revises: j6e7f8a9b0c1
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "j6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_profiles",
        sa.Column(
            "workflow_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_profiles", "workflow_enabled")
