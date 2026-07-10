"""Per-service, per-connector integration action overrides.

``services.integration_action_overrides`` is a JSON object keyed by integration
connector id, e.g. ``{"<connector_id>": {"ticket_lifecycle": false}}``. Only an
explicit ``false`` disables an action for the service; absent/true keeps the
connector default, so existing services keep their current behavior. Lets a
service keep an integration available to the agent while opting out of its
automatic ticket lifecycle (auto-open + status sync).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column(
                "integration_action_overrides",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
    op.alter_column("services", "integration_action_overrides", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_column("integration_action_overrides")
