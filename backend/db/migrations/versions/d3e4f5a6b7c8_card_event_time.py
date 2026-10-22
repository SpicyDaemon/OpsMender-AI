"""Keep card event time separate from delivery receipt time.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incident_notification_receipts") as batch:
        batch.add_column(
            sa.Column("event_generated_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("incident_notification_receipts") as batch:
        batch.drop_column("event_generated_at")
