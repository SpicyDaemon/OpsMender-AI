"""Add webhook_triggers table.

Revision ID: f1a2b3c4d5e6
Revises: e4a1d9f2b5c6
Create Date: 2026-04-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e4a1d9f2b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), unique=True, nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column(
            "event_types",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column(
            "headers",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("webhook_triggers")
