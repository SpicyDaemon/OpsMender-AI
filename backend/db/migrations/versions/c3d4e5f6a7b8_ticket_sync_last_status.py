"""Track the last internal status synced onto a ticket (no-backward guardrail).

``ticket_sync_state.last_synced_status`` records the OpsMender lifecycle status
(open / acknowledged / in_progress / resolved) most recently pushed to the
external ticket, so the outbound sync can refuse to move a ticket *backward*
(e.g. a reopen must not drag a Done ticket back to In Progress), matching how
PagerDuty forbids backward status moves. Nullable; existing rows backfill as
NULL (treated as "no prior sync", so the next sync always applies).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket_sync_state",
        sa.Column("last_synced_status", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticket_sync_state", "last_synced_status")
