"""Announce an operator's Maintenance Window when it becomes active.

``maintenance_windows.announce_on_start`` is set for windows an operator
creates; ``start_announced_at`` records the one Inbox notice to the covered
teams and admins (O-05). Existing windows are not announced.

Downgrade drops both columns.

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "x1y2z3a4b5c6"
down_revision = "w0x1y2z3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_windows",
        sa.Column(
            "announce_on_start",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "maintenance_windows",
        sa.Column("start_announced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maintenance_windows", "start_announced_at")
    op.drop_column("maintenance_windows", "announce_on_start")
