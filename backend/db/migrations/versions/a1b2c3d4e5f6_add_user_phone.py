"""Add an optional phone number to users for SMS / Voice Call paging.

Nullable, stored as entered (``+`` and digits only). Used as the default
recipient for the ``sms`` / ``voice`` personal-routing channels when no
per-channel address is configured. Existing rows are unaffected.

Revision ID: a1b2c3d4e5f6
Revises: p2q3r4s5t6u7
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "p2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
