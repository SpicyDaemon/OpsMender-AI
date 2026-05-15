"""Add escalation chains + ack lifecycle (Sprint 34).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-15 00:00:01.000000

Sprint 34 adds the paging engine on top of Sprint 33's foundation: chain
definitions (``escalation_chains`` + ``escalation_steps``), per-service
chain selection (``service_escalation_chains``), per-incident chain run
state (``incident_chain_states``), and a per-page audit log
(``incident_pages``). Actual notification delivery to humans is wired in
Sprint 35; Sprint 34's chain engine is testable via the page log alone.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "escalation_chains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_escalation_chains_team_id",
        "escalation_chains",
        ["team_id"],
        unique=False,
    )

    op.create_table(
        "escalation_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column(
            "timeout_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column("notify_channels", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["escalation_chains.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chain_id", "step_index", name="uq_escalation_step_index"
        ),
    )
    op.create_index(
        "ix_escalation_steps_chain_id",
        "escalation_steps",
        ["chain_id"],
        unique=False,
    )

    op.create_table(
        "service_escalation_chains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.Uuid(), nullable=False),
        sa.Column("applies_when", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["escalation_chains.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_id", "chain_id", name="uq_service_escalation_chain"
        ),
    )

    op.create_table(
        "incident_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.Uuid(), nullable=True),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column(
            "channel",
            sa.String(length=40),
            nullable=False,
            server_default="recorded",
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_via", sa.String(length=20), nullable=True),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            nullable=False,
            server_default="recorded",
        ),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["escalation_chains.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_pages_incident_id",
        "incident_pages",
        ["incident_id"],
        unique=False,
    )
    op.create_index(
        "ix_incident_pages_sent_at",
        "incident_pages",
        ["sent_at"],
        unique=False,
    )

    op.create_table(
        "incident_chain_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "current_step_index",
            sa.Integer(),
            nullable=False,
            server_default="-1",
        ),
        sa.Column("next_step_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hard_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_takeover_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "pending_takeover_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["escalation_chains.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pending_takeover_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id"),
    )


def downgrade() -> None:
    op.drop_table("incident_chain_states")
    op.drop_index("ix_incident_pages_sent_at", table_name="incident_pages")
    op.drop_index("ix_incident_pages_incident_id", table_name="incident_pages")
    op.drop_table("incident_pages")
    op.drop_table("service_escalation_chains")
    op.drop_index(
        "ix_escalation_steps_chain_id", table_name="escalation_steps"
    )
    op.drop_table("escalation_steps")
    op.drop_index(
        "ix_escalation_chains_team_id", table_name="escalation_chains"
    )
    op.drop_table("escalation_chains")
