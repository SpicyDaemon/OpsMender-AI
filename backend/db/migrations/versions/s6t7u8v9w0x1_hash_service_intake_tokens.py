"""Keep only a hash of each service's intake secret.

The secret in a service's intake URL used to sit in ``services.intake_token``
as plain text, so anyone who could read the table could post alerts and page a
team (S-108). The table now keeps the SHA-256 hash that ``ingest_tokens`` also
uses, plus the token's first eight characters as a hint. Existing URLs keep
working: their hashes are computed here. The full URL is shown once, on create
and on rotate.

Downgrade restores an empty column. A raw token can't be recovered from its
hash, so every service needs a rotated URL after a downgrade.

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "s6t7u8v9w0x1"
down_revision = "r5s6t7u8v9w0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch:
        batch.add_column(sa.Column("intake_token_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("intake_token_hint", sa.String(16), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, intake_token FROM services WHERE intake_token IS NOT NULL")
    ).fetchall()
    for service_id, token in rows:
        connection.execute(
            sa.text(
                "UPDATE services SET intake_token_hash = :token_hash, "
                "intake_token_hint = :hint WHERE id = :service_id"
            ),
            {
                "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "hint": token[:8],
                "service_id": service_id,
            },
        )

    op.drop_index("ix_services_intake_token", table_name="services")
    with op.batch_alter_table("services") as batch:
        batch.drop_column("intake_token")
    op.create_index(
        "ix_services_intake_token_hash",
        "services",
        ["intake_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_services_intake_token_hash", table_name="services")
    with op.batch_alter_table("services") as batch:
        batch.add_column(sa.Column("intake_token", sa.String(160), nullable=True))
        batch.drop_column("intake_token_hint")
        batch.drop_column("intake_token_hash")
    op.create_index(
        "ix_services_intake_token", "services", ["intake_token"], unique=True
    )
