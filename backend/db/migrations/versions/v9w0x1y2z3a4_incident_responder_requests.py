"""Requests for people from other teams to join an incident as responders.

``incident_responder_requests`` records who was asked, by whom, and how it
ended (pending, accepted, declined, expired or cancelled). At most one
request per incident and person is pending at a time.

Downgrade drops the table; accepted requests already became responders.

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v9w0x1y2z3a4"
down_revision = "u8v9w0x1y2z3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_responder_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_responder_requests_incident_id",
        "incident_responder_requests",
        ["incident_id"],
        unique=False,
    )
    op.create_index(
        "ix_incident_responder_requests_pending",
        "incident_responder_requests",
        ["incident_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_incident_responder_requests_due",
        "incident_responder_requests",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_responder_requests_due", table_name="incident_responder_requests"
    )
    op.drop_index(
        "ix_incident_responder_requests_pending",
        table_name="incident_responder_requests",
    )
    op.drop_index(
        "ix_incident_responder_requests_incident_id",
        table_name="incident_responder_requests",
    )
    op.drop_table("incident_responder_requests")
