"""Report schedules keep their day and local time.

``report_schedules.time_zone`` is where the schedule's day and time are read;
``run_day`` and ``run_time`` remember the configured day of the month and
local time, so a monthly report set for the 31st runs on the last day of
shorter months and returns to the 31st, and a 09:00 report stays at 09:00
across clock changes (R-18). Existing schedules read UTC and take their day
and time from their next run.

Downgrade drops the three columns.

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_schedules",
        sa.Column(
            "time_zone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
    )
    op.add_column("report_schedules", sa.Column("run_day", sa.Integer(), nullable=True))
    op.add_column(
        "report_schedules", sa.Column("run_time", sa.String(length=5), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("report_schedules", "run_time")
    op.drop_column("report_schedules", "run_day")
    op.drop_column("report_schedules", "time_zone")
