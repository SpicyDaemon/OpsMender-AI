"""Widen slos.objective_pct to NUMERIC(6,3) for 3-decimal SLA precision.

The original NUMERIC(5,4) column maxed out at 9.9999 and would overflow on
PostgreSQL for any realistic objective (e.g. 99.9 or 99.999). Widen to
NUMERIC(6,3) so values like 99.999 and 100.000 store and round-trip cleanly.

Revision ID: p5q6r7s8t9u0
Revises: n4o5p6q7r8s9
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "p5q6r7s8t9u0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite does not enforce NUMERIC precision and cannot ALTER COLUMN TYPE
    # in-place; the type is cosmetic there, so skip the no-op alter.
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "slos",
        "objective_pct",
        existing_type=sa.Numeric(5, 4),
        type_=sa.Numeric(6, 3),
        existing_nullable=False,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "slos",
        "objective_pct",
        existing_type=sa.Numeric(6, 3),
        type_=sa.Numeric(5, 4),
        existing_nullable=False,
    )
