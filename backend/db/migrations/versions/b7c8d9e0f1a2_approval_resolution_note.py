"""Add approval_requests.resolution_note for Tier 1 redirect/reject guidance.

Tier 1 becomes interactive: every write action is routed through the operator
approval gate, and the operator may approve, reject, or **redirect** with
free-text steering. ``resolution_note`` stores that operator guidance so the
workflow can re-plan with it.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "resolution_note")
