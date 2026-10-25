"""Drop the environment scan audit tables (M1-93, owner decision O-27).

The environment scan audits are removed from the product: the audit runs,
their findings and the audit schedules go with them. Upgrading deletes any
rows those tables still hold. The downgrade recreates the three tables empty,
with the columns, constraints and indexes they had at revision d3e4f5a6b7c8;
the rows are not restored.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_audit_findings_created_at", table_name="audit_findings")
    op.drop_index("ix_audit_findings_run_id", table_name="audit_findings")
    op.drop_table("audit_findings")
    op.drop_index("ix_audit_runs_created_at", table_name="audit_runs")
    op.drop_table("audit_runs")
    op.drop_index("ix_audit_schedules_due", table_name="audit_schedules")
    op.drop_table("audit_schedules")


def downgrade() -> None:
    op.create_table(
        "audit_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("analyzers", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("mcp_server_name", sa.String(length=200), nullable=True),
        sa.Column("focus_areas", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "interval_minutes >= 15", name="ck_audit_schedules_interval_min"
        ),
    )
    op.create_index(
        "ix_audit_schedules_due", "audit_schedules", ["is_active", "next_run_at"]
    )

    op.create_table(
        "audit_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("analyzers", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_runs_created_at", "audit_runs", ["created_at"])

    op.create_table(
        "audit_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("analyzer", sa.String(length=100), nullable=False),
        sa.Column(
            "severity", sa.String(length=20), nullable=False, server_default="info"
        ),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("resource", sa.String(length=500), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="open"
        ),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_findings_run_id", "audit_findings", ["run_id"])
    op.create_index("ix_audit_findings_created_at", "audit_findings", ["created_at"])
