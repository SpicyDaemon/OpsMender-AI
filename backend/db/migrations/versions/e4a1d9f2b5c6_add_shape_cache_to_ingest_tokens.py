"""add shape_cache column to ingest_tokens

Revision ID: e4a1d9f2b5c6
Revises: d4f8b9c0e534
Create Date: 2026-04-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e4a1d9f2b5c6"
down_revision: Union[str, Sequence[str], None] = "d4f8b9c0e534"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add shape_cache column used by the Universal ingest adapter."""
    op.add_column(
        "ingest_tokens",
        sa.Column(
            "shape_cache",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_tokens", "shape_cache")
