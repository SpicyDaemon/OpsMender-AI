"""Add Slack per-incident channel mirror (Sprint 36 step 5).

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-05-17 00:00:01.000000

Sprint 36 step 5 lets operators opt into a Slack workspace channel per
paged incident. When the chain starts in ``page`` mode and the org has
``slack_incident_channels_enabled`` set, OpsMender calls
``conversations.create`` to make a deterministic ``inc-<short>`` channel
and stores the resulting channel id on the incident so later updates can
mirror to the same place.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "slack_incident_channels_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "incidents",
        sa.Column("slack_channel_id", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "incidents",
        sa.Column("slack_channel_name", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("incidents", "slack_channel_name")
    op.drop_column("incidents", "slack_channel_id")
    op.drop_column("organizations", "slack_incident_channels_enabled")
