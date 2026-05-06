"""Add organization_domains for host-based tenant routing.

Revision ID: f5a8b3c1d9e2
Revises: 4e96f612bade
Create Date: 2026-05-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5a8b3c1d9e2"
down_revision: Union[str, Sequence[str], None] = "4e96f612bade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_domains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_organization_domains_domain"),
    )
    op.create_index(
        "ix_organization_domains_org_id",
        "organization_domains",
        ["org_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_organization_domains_org_id", table_name="organization_domains")
    op.drop_table("organization_domains")
