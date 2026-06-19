"""Remove the incident ``closed`` lifecycle status.

Resolved is now the final incident state. Existing closed incidents are
normalized to resolved during upgrade.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from __future__ import annotations

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE incidents SET status = 'resolved' WHERE status = 'closed'")


def downgrade() -> None:
    # Former closed rows cannot be distinguished from ordinary resolved rows
    # after normalization, so downgrade intentionally preserves resolved.
    pass
