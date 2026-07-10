"""Add org_sso_configs for per-tenant SSO/OIDC.

Revision ID: a8c4d2e1f9b3
Revises: f5a8b3c1d9e2
Create Date: 2026-05-06 00:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c4d2e1f9b3"
down_revision: Union[str, Sequence[str], None] = "f5a8b3c1d9e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "org_sso_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovery_url", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            sa.String(length=255),
            nullable=False,
            server_default="openid email profile",
        ),
        sa.Column(
            "email_claim", sa.String(length=64), nullable=False, server_default="email"
        ),
        sa.Column(
            "name_claim", sa.String(length=64), nullable=False, server_default="name"
        ),
        sa.Column(
            "default_role",
            sa.String(length=20),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("allowed_email_domains", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_org_sso_configs_org_id"),
    )


def downgrade() -> None:
    op.drop_table("org_sso_configs")
