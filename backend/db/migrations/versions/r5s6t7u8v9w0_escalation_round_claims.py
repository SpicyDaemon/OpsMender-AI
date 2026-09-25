"""Make escalation page claims run-scoped and uniquely recorded.

Historical logical markers are retained. For duplicate (incident, user, step)
markers the earliest sent_at/id stays `recorded`; later markers become
`recorded_legacy`. Physical channel attempts and acknowledgement data are not
changed. A pre-upgrade chain with recorded history starts at round 1, so its
next claim cannot collide with a marker from any earlier handoff at round 0.
The obsolete hard_deadline_at column remains nullable and unused.

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r5s6t7u8v9w0"
down_revision = "q4r5s6t7u8v9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incident_pages") as batch:
        batch.add_column(
            sa.Column("round", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("incident_chain_states") as batch:
        batch.add_column(
            sa.Column("round", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "exhaustion_notified_at", sa.DateTime(timezone=True), nullable=True
            )
        )

    connection = op.get_bind()
    connection.execute(
        sa.text("""
        UPDATE incident_chain_states AS state SET round = 1
        WHERE EXISTS (
            SELECT 1 FROM incident_pages AS page
            WHERE page.incident_id = state.incident_id
              AND page.channel = 'recorded'
              AND page.step_index IS NOT NULL
        )
    """)
    )

    duplicate_ids = (
        connection.execute(
            sa.text("""
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY incident_id, user_id, step_index
                ORDER BY sent_at, id
            ) AS position
            FROM incident_pages
            WHERE channel = 'recorded' AND step_index IS NOT NULL
        ) AS ranked WHERE position > 1
    """)
        )
        .scalars()
        .all()
    )
    pages = sa.table(
        "incident_pages", sa.column("id", sa.Uuid()), sa.column("channel", sa.String())
    )
    for page_id in duplicate_ids:
        connection.execute(
            pages.update()
            .where(pages.c.id == page_id)
            .values(channel="recorded_legacy")
        )

    op.create_index(
        "uq_incident_pages_recorded_round",
        "incident_pages",
        ["incident_id", "user_id", "step_index", "round"],
        unique=True,
        postgresql_where=sa.text("channel = 'recorded' AND step_index IS NOT NULL"),
        sqlite_where=sa.text("channel = 'recorded' AND step_index IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_incident_pages_recorded_round", table_name="incident_pages")
    with op.batch_alter_table("incident_chain_states") as batch:
        batch.drop_column("exhaustion_notified_at")
        batch.drop_column("round")
    with op.batch_alter_table("incident_pages") as batch:
        batch.drop_column("round")
