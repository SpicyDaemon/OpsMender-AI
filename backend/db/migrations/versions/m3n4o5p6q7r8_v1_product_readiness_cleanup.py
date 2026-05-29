"""v1 product readiness cleanup fields.

Adds service-owned alert-intake and routing metadata plus explicit roster
coverage windows while keeping legacy paging tables/routes available.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m3n4o5p6q7r8"
down_revision: Union[str, Sequence[str], None] = "l2m3n4o5p6q7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column(
            "priority",
            sa.String(length=8),
            nullable=False,
            server_default="P2",
        ),
    )
    op.add_column(
        "services",
        sa.Column("intake_token", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "services",
        sa.Column(
            "preferred_mcp_server_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        "ix_services_intake_token",
        "services",
        ["intake_token"],
        unique=True,
    )
    op.alter_column("services", "priority", server_default=None)
    op.alter_column("services", "preferred_mcp_server_ids", server_default=None)

    op.add_column(
        "rosters",
        sa.Column(
            "coverage_start_time",
            sa.String(length=8),
            nullable=False,
            server_default="09:00",
        ),
    )
    op.add_column(
        "rosters",
        sa.Column(
            "coverage_end_time",
            sa.String(length=8),
            nullable=False,
            server_default="17:00",
        ),
    )
    op.alter_column("rosters", "coverage_start_time", server_default=None)
    op.alter_column("rosters", "coverage_end_time", server_default=None)


def downgrade() -> None:
    op.drop_column("rosters", "coverage_end_time")
    op.drop_column("rosters", "coverage_start_time")
    op.drop_index("ix_services_intake_token", table_name="services")
    op.drop_column("services", "preferred_mcp_server_ids")
    op.drop_column("services", "intake_token")
    op.drop_column("services", "priority")
