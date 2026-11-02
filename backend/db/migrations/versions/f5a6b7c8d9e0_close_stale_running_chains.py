"""Close escalation chains left running before the acknowledgement lock (M1-106).

Before the acknowledgement lock (D-021), a chain whose last level had fired
kept status ``running`` with no next due time and no finished time. The
engine never ticks such a row (nothing is due) and ``chain_is_live`` counts
it as live, so it showed as running for good and a Slack command without an
incident id could pick its incident at any age. Current code never writes
such a row.

Upgrading marks those rows exhausted, finished at the migration time; it
sends no exhaustion notice, so their ``exhaustion_notified_at`` stays empty.

The downgrade leaves the data as it is, as e4f5a6b7c8d9 does for its rows.
Nothing tells the rows this migration closed apart from chains a release
before 1.1.1 exhausted (those carry no notice stamp either: the column came
later with no backfill), and putting a stuck row back to running would only
recreate the bug for the older release, which finishes these chains fine.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None

chain_states = sa.table(
    "incident_chain_states",
    sa.column("status", sa.String()),
    sa.column("next_step_due_at", sa.DateTime(timezone=True)),
    sa.column("finished_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    op.execute(
        chain_states.update()
        .where(chain_states.c.status == "running")
        .where(chain_states.c.next_step_due_at.is_(None))
        .where(chain_states.c.finished_at.is_(None))
        .values(status="exhausted", finished_at=now)
    )


def downgrade() -> None:
    """The closed chains stay closed (see the module docstring)."""
