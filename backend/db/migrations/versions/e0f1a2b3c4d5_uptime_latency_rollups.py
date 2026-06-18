"""Response-time rollups — latency on downsampled uptime tables (v1.2).

Adds avg/min/max latency columns to ``uptime_samples_5m`` and
``uptime_samples_1h`` so response-time history survives raw-sample pruning
(raw samples, the only place latency lived, are pruned after 30 days) and can
span up to 365 days. Backfill is not possible (old rollups have no latency);
the columns fill going forward.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None

_TABLES = ("uptime_samples_5m", "uptime_samples_1h")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column("avg_latency_ms", sa.Numeric(10, 2), nullable=True)
            )
            batch_op.add_column(
                sa.Column("min_latency_ms", sa.Integer(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("max_latency_ms", sa.Integer(), nullable=True)
            )
            batch_op.add_column(
                sa.Column(
                    "latency_samples",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("latency_samples")
            batch_op.drop_column("max_latency_ms")
            batch_op.drop_column("min_latency_ms")
            batch_op.drop_column("avg_latency_ms")
