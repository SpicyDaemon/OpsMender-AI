"""Add an optional uploaded profile picture to users.

``users.avatar_image`` holds the normalized PNG bytes (resized to fit 200x200
server-side); ``users.avatar_image_updated_at`` records when it last changed
(cache-busting). Both nullable — absence falls back to the generated initials
avatar. Existing rows are unaffected.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("avatar_image", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "avatar_image_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar_image_updated_at")
    op.drop_column("users", "avatar_image")
