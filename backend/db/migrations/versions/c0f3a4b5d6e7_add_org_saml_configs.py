"""Add org_saml_configs for per-tenant SAML 2.0 SSO.

Revision ID: c0f3a4b5d6e7
Revises: b9d7e6c5a4f3
Create Date: 2026-05-07 00:00:00.000000

Sibling table to ``org_sso_configs``. Per Sprint 30 locked decisions, OIDC
and SAML get separate tables — the columns don't meaningfully overlap, and
keeping them apart preserves NOT NULL constraints on each side.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c0f3a4b5d6e7"
down_revision: Union[str, Sequence[str], None] = "b9d7e6c5a4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "org_saml_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        # IdP can be supplied as a metadata URL (preferred — auto-fetched and
        # cached) or as inline raw XML pasted by the admin. Exactly one of
        # these is required at runtime; the API enforces the XOR.
        sa.Column("idp_metadata_url", sa.Text(), nullable=True),
        sa.Column("idp_metadata_xml", sa.Text(), nullable=True),
        # JIT provisioning + claim mapping (mirrors org_sso_configs).
        sa.Column(
            "email_attribute",
            sa.String(length=128),
            nullable=False,
            server_default="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        ),
        sa.Column(
            "name_attribute",
            sa.String(length=128),
            nullable=False,
            server_default="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        ),
        sa.Column(
            "default_role",
            sa.String(length=20),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("allowed_email_domains", sa.Text(), nullable=True),
        # Whether the IdP signs AuthnResponses / individual assertions. Most
        # IdPs sign the response; a few sign only the assertion. OpsMender requires
        # at least one.
        sa.Column(
            "want_assertions_signed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "want_response_signed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_org_saml_configs_org_id"),
    )


def downgrade() -> None:
    op.drop_table("org_saml_configs")
