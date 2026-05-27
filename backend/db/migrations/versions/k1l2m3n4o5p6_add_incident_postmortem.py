"""Add postmortem_md + postmortem_updated_at to incidents.

Sprint 61 Step 4 — postmortem authoring surface. A single markdown column
holds the operator-authored postmortem; a separate updated_at column
lets the UI show "last edited" without conflating it with the incident's
own updated_at clock.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("postmortem_md", sa.Text(), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column(
            "postmortem_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("incidents", "postmortem_updated_at")
    op.drop_column("incidents", "postmortem_md")
