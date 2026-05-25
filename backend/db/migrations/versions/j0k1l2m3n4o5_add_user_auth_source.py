"""Add users.auth_source for People auth badges.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_source",
            sa.String(length=255),
            nullable=False,
            server_default="local",
        ),
    )
    op.execute("UPDATE users SET auth_source = 'local' WHERE auth_source IS NULL")
    op.alter_column("users", "auth_source", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "auth_source")
