"""Keep removed Roster members' places in the rotation.

``roster_members.removed_at`` marks a member taken off a Roster (removed,
deactivated or demoted to Viewer). The row keeps its position, so the
others keep their shifts and the removed member's shifts pass to the next
member (O-03, R-13). Existing rows stay members.

Downgrade deletes removed rows, then drops the column.

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "w0x1y2z3a4b5"
down_revision = "v9w0x1y2z3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roster_members",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("DELETE FROM roster_members WHERE removed_at IS NOT NULL")
    op.drop_column("roster_members", "removed_at")
