"""Reassign incidents to another team, and ask up to three responders.

``incidents.team_id`` records a reassignment: when set, that team handles the
incident and its Escalation Chain pages; when null, the service's team does.
``incident_responders`` lists the people asked to help besides the owner.

Downgrade drops both. Incidents fall back to their service's team.

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t7u8v9w0x1y2"
down_revision = "s6t7u8v9w0x1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(sa.Column("team_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_incidents_team_id",
            "teams",
            ["team_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "incident_responders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "user_id", name="uq_incident_responder"),
    )
    op.create_index(
        "ix_incident_responders_incident_id",
        "incident_responders",
        ["incident_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_responders_incident_id", table_name="incident_responders"
    )
    op.drop_table("incident_responders")
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_constraint("fk_incidents_team_id", type_="foreignkey")
        batch_op.drop_column("team_id")
