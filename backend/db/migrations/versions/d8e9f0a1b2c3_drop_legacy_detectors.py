"""Drop legacy detector tables (Sprint 39 step 4).

Revision ID: d8e9f0a1b2c3
Revises: d7e8f9a0b1c2
Create Date: 2026-05-17 18:00:00.000000

Sprint 39 step 4 retires the Detector runtime and dashboard. Operators
with existing detector rules should run ``opsmender detectors-migrate
--apply`` before upgrading through this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_detector_history_rule_id", table_name="detector_history")
    op.drop_table("detector_history")
    op.drop_table("detector_rules")


def downgrade() -> None:
    op.create_table(
        "detector_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "mcp_server_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column(
            "model_config_id",
            sa.Uuid(),
            sa.ForeignKey("model_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("severity_default", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_ran_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fingerprint", sa.String(length=500), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "name", name="uq_detector_rule_name"),
    )
    op.create_table(
        "detector_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            sa.Uuid(),
            sa.ForeignKey("detector_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("issue_detected", sa.Boolean(), nullable=False),
        sa.Column(
            "incident_id",
            sa.Uuid(),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_verdict", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_detector_history_rule_id", "detector_history", ["rule_id"]
    )
