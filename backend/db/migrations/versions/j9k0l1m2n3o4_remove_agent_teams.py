"""Remove agent team profiles.

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
"""

from alembic import op
import sqlalchemy as sa


revision = "j9k0l1m2n3o4"
down_revision = "i8j9k0l1m2n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_sessions_agent_team_profiles",
            type_="foreignkey",
        )
        batch_op.drop_column("agent_team_profile_id")

    op.drop_table("agent_team_profiles")


def downgrade() -> None:
    op.create_table(
        "agent_team_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=False),
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
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "name", name="uq_agent_team_profile_name"),
    )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column("agent_team_profile_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_sessions_agent_team_profiles",
            "agent_team_profiles",
            ["agent_team_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )
