"""Add profile fields to users: first_name, last_name, avatar_color.

These power user profiles + the generated initials avatar (no file storage in
v1). All nullable so existing rows are unaffected.

Revision ID: q6r7s8t9u0v1
Revises: p5q6r7s8t9u0
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q6r7s8t9u0v1"
down_revision = "p5q6r7s8t9u0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("avatar_color", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_color")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
