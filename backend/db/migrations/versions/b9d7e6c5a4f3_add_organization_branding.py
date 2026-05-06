"""Add organization branding column.

Revision ID: b9d7e6c5a4f3
Revises: a8c4d2e1f9b3
Create Date: 2026-05-06 13:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9d7e6c5a4f3"
down_revision: Union[str, Sequence[str], None] = "a8c4d2e1f9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("organizations")}
    if "branding" not in columns:
        op.add_column("organizations", sa.Column("branding", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("organizations")}
    if "branding" in columns:
        op.drop_column("organizations", "branding")
