"""Add agent_team_profiles and link sessions to them.

Revision ID: e7f6d5c4b3a2
Revises: c9d8e7f6a5b4
Create Date: 2026-04-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e7f6d5c4b3a2"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_team_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "roles",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "agent_team_profile_id",
                sa.Uuid(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_sessions_agent_team_profile_id",
            "agent_team_profiles",
            ["agent_team_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_sessions_agent_team_profile_id", type_="foreignkey"
        )
        batch_op.drop_column("agent_team_profile_id")

    op.drop_table("agent_team_profiles")
