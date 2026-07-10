"""add api_version to model_configs

Revision ID: 7e6d4d2df3a1
Revises: d256afa022b4
Create Date: 2026-04-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "7e6d4d2df3a1"
down_revision = "d256afa022b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs", sa.Column("api_version", sa.String(length=50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("model_configs", "api_version")
