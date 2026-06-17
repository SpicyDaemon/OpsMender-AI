"""Link SLA targets to a Service (v1.2 Phase 6).

Adds a nullable ``service_id`` FK to ``sla_targets`` so SLO-breach
recommendations can route to the owning service's team / on-call.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sla_targets") as batch_op:
        batch_op.add_column(sa.Column("service_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_sla_targets_service_id",
            "services",
            ["service_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sla_targets") as batch_op:
        batch_op.drop_constraint("fk_sla_targets_service_id", type_="foreignkey")
        batch_op.drop_column("service_id")
