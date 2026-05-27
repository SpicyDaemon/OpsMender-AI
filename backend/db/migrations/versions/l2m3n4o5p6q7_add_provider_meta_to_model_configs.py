"""Add provider_meta to model_configs.

Sprint 62 Step 2 — provider-specific non-secret settings (starting with
AWS Bedrock region/profile) need a durable home without forcing a new
column for every cloud provider integration.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("provider_meta", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "provider_meta")
