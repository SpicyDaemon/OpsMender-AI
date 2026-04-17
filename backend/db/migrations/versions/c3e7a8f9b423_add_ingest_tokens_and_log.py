"""add ingest_tokens, ingest_log tables and external fingerprint on incidents

Revision ID: c3e7a8f9b423
Revises: b2d9f5e6c312
Create Date: 2026-04-17 03:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3e7a8f9b423'
down_revision: Union[str, Sequence[str], None] = 'b2d9f5e6c312'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add external fingerprint columns to incidents + ingest tables."""

    # -- incidents: external fingerprint columns -----------------------------
    op.add_column(
        "incidents",
        sa.Column("external_id", sa.String(500), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("external_source", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_incidents_external_fingerprint",
        "incidents",
        ["external_source", "external_id"],
        unique=False,
    )

    # -- ingest_tokens -------------------------------------------------------
    op.create_table(
        "ingest_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), unique=True, nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # -- ingest_log ----------------------------------------------------------
    op.create_table(
        "ingest_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingest_token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingest_tokens.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id"),
            nullable=True,
        ),
        sa.Column("dedup_action", sa.String(20), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_ingest_log_token_id", "ingest_log", ["ingest_token_id"]
    )
    op.create_index(
        "ix_ingest_log_created_at", "ingest_log", ["created_at"]
    )


def downgrade() -> None:
    """Remove ingest tables and external fingerprint columns."""
    op.drop_index("ix_ingest_log_created_at", table_name="ingest_log")
    op.drop_index("ix_ingest_log_token_id", table_name="ingest_log")
    op.drop_table("ingest_log")
    op.drop_table("ingest_tokens")
    op.drop_index("ix_incidents_external_fingerprint", table_name="incidents")
    op.drop_column("incidents", "external_source")
    op.drop_column("incidents", "external_id")
