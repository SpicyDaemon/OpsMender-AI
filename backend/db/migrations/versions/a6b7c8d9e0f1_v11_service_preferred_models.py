"""Add service preferred models and incident ingestion model.

Revision ID: a6b7c8d9e0f1
Revises: z5a6b7c8d9e0
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "z5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("model_configs", "is_active", server_default=None)

    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column(
                "preferred_model_config_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.drop_column("ai_auto_start_enabled")
    op.alter_column(
        "services",
        "preferred_model_config_ids",
        server_default=None,
    )

    op.add_column(
        "incidents",
        sa.Column("ingestion_model_config_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_incidents_ingestion_model_config",
        "incidents",
        "model_configs",
        ["ingestion_model_config_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_incidents_ingestion_model_config",
        "incidents",
        type_="foreignkey",
    )
    op.drop_column("incidents", "ingestion_model_config_id")
    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column("ai_auto_start_enabled", sa.Boolean(), nullable=True)
        )
        batch_op.drop_column("preferred_model_config_ids")
    op.drop_column("model_configs", "is_active")
