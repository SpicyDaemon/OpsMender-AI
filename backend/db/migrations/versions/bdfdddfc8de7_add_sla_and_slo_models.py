"""Add SLA and SLO models

Revision ID: bdfdddfc8de7
Revises: e7f6d5c4b3a2
Create Date: 2026-04-25 12:17:47.165854

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'bdfdddfc8de7'
down_revision: Union[str, Sequence[str], None] = 'e7f6d5c4b3a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sla_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("owner_team", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "uptime_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sla_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("up", sa.Boolean, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("suppressed", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_uptime_samples_target_id", "uptime_samples", ["target_id"])
    op.create_index("ix_uptime_samples_observed_at", "uptime_samples", ["observed_at"])

    op.create_table(
        "uptime_samples_5m",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sla_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("up_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("total_samples", sa.Integer, nullable=False),
    )
    op.create_index("ix_uptime_samples_5m_target_id", "uptime_samples_5m", ["target_id"])
    op.create_index("ix_uptime_samples_5m_bucket_start", "uptime_samples_5m", ["bucket_start"])

    op.create_table(
        "uptime_samples_1h",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sla_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("up_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("total_samples", sa.Integer, nullable=False),
    )
    op.create_index("ix_uptime_samples_1h_target_id", "uptime_samples_1h", ["target_id"])
    op.create_index("ix_uptime_samples_1h_bucket_start", "uptime_samples_1h", ["bucket_start"])

    op.create_table(
        "slos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sla_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("objective_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("window_seconds", sa.Integer, nullable=False),
        sa.Column("burn_alert_threshold", sa.Numeric(10, 4), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_slos_target_id", "slos", ["target_id"])

    op.create_table(
        "maintenance_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rrule", sa.Text, nullable=True),
        sa.Column("target_ids", postgresql.JSONB, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("maintenance_windows")
    op.drop_index("ix_slos_target_id", table_name="slos")
    op.drop_table("slos")
    op.drop_index("ix_uptime_samples_observed_at", table_name="uptime_samples")
    op.drop_index("ix_uptime_samples_target_id", table_name="uptime_samples")
    op.drop_table("uptime_samples")
    op.drop_index("ix_uptime_samples_1h_bucket_start", table_name="uptime_samples_1h")
    op.drop_index("ix_uptime_samples_1h_target_id", table_name="uptime_samples_1h")
    op.drop_table("uptime_samples_1h")
    op.drop_index("ix_uptime_samples_5m_bucket_start", table_name="uptime_samples_5m")
    op.drop_index("ix_uptime_samples_5m_target_id", table_name="uptime_samples_5m")
    op.drop_table("uptime_samples_5m")
    op.drop_table("sla_targets")
