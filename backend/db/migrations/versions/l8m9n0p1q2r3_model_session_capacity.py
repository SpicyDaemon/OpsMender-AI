"""Add per-model incident-session capacity.

Revision ID: l8m9n0p1q2r3
Revises: k7f8a9b0c1d2
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l8m9n0p1q2r3"
down_revision: Union[str, Sequence[str], None] = "k7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_configs") as batch_op:
        batch_op.add_column(
            sa.Column("max_concurrent_sessions", sa.Integer(), nullable=True),
        )
        batch_op.create_check_constraint(
            "ck_model_configs_max_concurrent_sessions_nonnegative",
            "max_concurrent_sessions IS NULL OR max_concurrent_sessions >= 0",
        )
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column("model_config_id", sa.Uuid(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_sessions_model_config_id_model_configs",
            "model_configs",
            ["model_config_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_sessions_model_config_id",
        "sessions",
        ["model_config_id"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_org_model_config_status",
        "sessions",
        ["org_id", "model_config_id", "status"],
        unique=False,
    )

    # Existing sessions stored only provider/model id. Backfill the FK only
    # when that pair identifies exactly one config in the same organization;
    # ambiguous historical rows remain nullable rather than guessing.
    op.execute(
        """
        UPDATE sessions
        SET model_config_id = (
            SELECT model_configs.id
            FROM model_configs
            WHERE model_configs.org_id = sessions.org_id
              AND model_configs.provider = sessions.model_provider
              AND model_configs.model_id = sessions.model_id
        )
        WHERE sessions.model_config_id IS NULL
          AND 1 = (
            SELECT COUNT(*)
            FROM model_configs
            WHERE model_configs.org_id = sessions.org_id
              AND model_configs.provider = sessions.model_provider
              AND model_configs.model_id = sessions.model_id
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_org_model_config_status", table_name="sessions")
    op.drop_index("ix_sessions_model_config_id", table_name="sessions")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_sessions_model_config_id_model_configs",
            type_="foreignkey",
        )
        batch_op.drop_column("model_config_id")
    with op.batch_alter_table("model_configs") as batch_op:
        batch_op.drop_constraint(
            "ck_model_configs_max_concurrent_sessions_nonnegative",
            type_="check",
        )
        batch_op.drop_column("max_concurrent_sessions")
