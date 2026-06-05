"""Add approval fields to maintenance_windows.

Operator-requested windows start unapproved (approved=False, approved_by=NULL)
and do not suppress alerts until an admin approves them. Admin-created windows
default to approved=True. Existing rows are backfilled to approved=True so
nothing breaks on upgrade.

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "u0v1w2x3y4z5"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_windows",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "maintenance_windows",
        sa.Column("approved_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "maintenance_windows",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_mw_approved_by_users",
        "maintenance_windows",
        "users",
        ["approved_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_mw_approved_by_users", "maintenance_windows", type_="foreignkey")
    op.drop_column("maintenance_windows", "approved_at")
    op.drop_column("maintenance_windows", "approved_by")
    op.drop_column("maintenance_windows", "approved")
