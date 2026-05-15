"""Add service_id to ingest_tokens (simplification pass).

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-05-15 02:00:00.000000

Per-service ingest endpoints: when an ingest token is bound to a service,
every incident created through that token gets ``service_id`` pre-filled
so the Sprint 34 paging engine can route to the owning team without the
operator having to encode the service in the alert payload.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingest_tokens", sa.Column("service_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ingest_tokens_service_id",
        "ingest_tokens",
        "services",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ingest_tokens_service_id", "ingest_tokens", type_="foreignkey"
    )
    op.drop_column("ingest_tokens", "service_id")
