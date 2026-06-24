"""Add a strict per-service integration allowlist.

``services.allowed_integration_connector_ids`` is a JSON array of the
integration connector ids a service is permitted to use. Empty means NO
integrations (strict allowlist semantics chosen by the owner).

To preserve behavior for existing deployments — where every service implicitly
had access to all connectors — this migration backfills each existing service
with every integration connector id in its organization. New services start
empty and must be granted access explicitly.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_integration_connector_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    # Backfill: each existing service keeps access to every connector in its
    # org so upgrades don't silently strip integrations. A typed lightweight
    # table lets SQLAlchemy serialize the JSON list correctly per-dialect.
    bind = op.get_bind()
    services_tbl = sa.table(
        "services",
        sa.column("id"),
        sa.column("org_id"),
        sa.column("allowed_integration_connector_ids", sa.JSON()),
    )
    connectors = bind.execute(
        sa.text("SELECT id, org_id FROM integration_connectors")
    ).fetchall()
    by_org: dict[str, list[str]] = defaultdict(list)
    for connector_id, org_id in connectors:
        by_org[str(org_id)].append(str(connector_id))

    services = bind.execute(sa.text("SELECT id, org_id FROM services")).fetchall()
    for service_id, org_id in services:
        ids = by_org.get(str(org_id), [])
        if not ids:
            continue
        bind.execute(
            services_tbl.update()
            .where(services_tbl.c.id == service_id)
            .values(allowed_integration_connector_ids=ids)
        )

    op.alter_column(
        "services",
        "allowed_integration_connector_ids",
        server_default=None,
    )


def downgrade() -> None:
    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_column("allowed_integration_connector_ids")
