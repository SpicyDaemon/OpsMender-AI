"""Add organization voice and SMS settings.

Revision ID: m0n1o2p3q4r5
Revises: l0m1n2o3p4q5
"""

from alembic import op
import sqlalchemy as sa


revision = "m0n1o2p3q4r5"
down_revision = "l0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_voice_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("account_sid", sa.String(length=255), nullable=True),
        sa.Column("auth_token_encrypted", sa.Text(), nullable=True),
        sa.Column("sms_from_number", sa.String(length=64), nullable=True),
        sa.Column("voice_from_number", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id"),
    )
    op.alter_column("org_voice_settings", "enabled", server_default=None)


def downgrade() -> None:
    op.drop_table("org_voice_settings")
