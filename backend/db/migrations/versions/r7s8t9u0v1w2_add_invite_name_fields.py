"""Add optional first_name / last_name to org_invites.

Lets an admin prefill the invitee's name; carried through to the created user
on acceptance. Nullable so existing invites are unaffected.

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r7s8t9u0v1w2"
down_revision = "q6r7s8t9u0v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "org_invites", sa.Column("first_name", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "org_invites", sa.Column("last_name", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("org_invites", "last_name")
    op.drop_column("org_invites", "first_name")
