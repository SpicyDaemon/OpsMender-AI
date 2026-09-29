"""Persist SAML replay claims and stable incident lifecycle timestamps.

Existing resolved incidents use their last updated_at as the best available
resolution timestamp. Existing service-chain links share the migration time,
so legacy ties retain UUID order; new links use their actual creation time.

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u8v9w0x1y2z3"
down_revision = "t7u8v9w0x1y2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE incidents SET resolved_at = updated_at WHERE status = 'resolved'"
    )
    with op.batch_alter_table("service_escalation_chains") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    op.create_table(
        "saml_assertion_replays",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("assertion_id_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id", "assertion_id_hash"),
    )
    op.create_index(
        "ix_saml_assertion_replays_expires_at",
        "saml_assertion_replays",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_saml_assertion_replays_expires_at", "saml_assertion_replays")
    op.drop_table("saml_assertion_replays")
    with op.batch_alter_table("service_escalation_chains") as batch_op:
        batch_op.drop_column("created_at")
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_column("resolved_at")
