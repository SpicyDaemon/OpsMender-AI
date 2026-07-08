"""Add alert-noise grouping and flapping state.

Revision ID: h7i8j9k0l1m2
Revises: g6h7i8j9k0l1
"""

from alembic import op
import sqlalchemy as sa


revision = "h7i8j9k0l1m2"
down_revision = "g6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "alert_grouping_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("services") as batch_op:
        batch_op.add_column(
            sa.Column(
                "alert_grouping",
                sa.String(length=10),
                nullable=False,
                server_default="inherit",
            )
        )

    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "correlated_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "flapping",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "alert_fingerprint_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=300), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "transitions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("flapping_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("incident_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_id",
            "fingerprint",
            name="uq_alert_fingerprint_state_service",
        ),
    )
    op.create_index(
        "ix_alert_fingerprint_states_org_id",
        "alert_fingerprint_states",
        ["org_id"],
    )
    op.create_index(
        "ix_alert_fingerprint_states_service_id",
        "alert_fingerprint_states",
        ["service_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_fingerprint_states_service_id",
        table_name="alert_fingerprint_states",
    )
    op.drop_index(
        "ix_alert_fingerprint_states_org_id",
        table_name="alert_fingerprint_states",
    )
    op.drop_table("alert_fingerprint_states")

    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_column("flapping")
        batch_op.drop_column("correlated_count")

    with op.batch_alter_table("services") as batch_op:
        batch_op.drop_column("alert_grouping")

    with op.batch_alter_table("organizations") as batch_op:
        batch_op.drop_column("alert_grouping_default")
