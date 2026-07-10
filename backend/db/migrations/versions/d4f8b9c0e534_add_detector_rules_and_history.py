"""Add detector_rules and detector_history tables.

Revision ID: d4f8b9c0e534
Revises: c3e7a8f9b423
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4f8b9c0e534"
down_revision = "c3e7a8f9b423"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detector_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column(
            "mcp_server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_template", sa.Text, nullable=False),
        sa.Column(
            "model_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("interval_seconds", sa.Integer, nullable=False, server_default="300"),
        sa.Column(
            "severity_default", sa.String(20), nullable=False, server_default="medium"
        ),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column("last_ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fingerprint", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "detector_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("detector_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column(
            "issue_detected",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_verdict", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )

    op.create_index("ix_detector_history_rule_id", "detector_history", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_detector_history_rule_id", table_name="detector_history")
    op.drop_table("detector_history")
    op.drop_table("detector_rules")
