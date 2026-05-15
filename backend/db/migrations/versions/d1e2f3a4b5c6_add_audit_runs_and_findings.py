"""Add audit_runs and audit_findings (Sprint 32 — Auditor v1).

Revision ID: d1e2f3a4b5c6
Revises: c0f3a4b5d6e7
Create Date: 2026-05-14 00:00:00.000000

Sprint 32 introduces the Auditor surface: read-only environment scans that
produce a separate ``audit_findings`` data model rather than reusing the
``incidents`` table. See ``docs/TASKS.md`` Sprint 32 for the locked decisions.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c0f3a4b5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        sa.Column(
            "finding_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_runs_created_at", "audit_runs", ["created_at"], unique=False
    )

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
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["audit_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_findings_run_id", "audit_findings", ["run_id"], unique=False
    )
    op.create_index(
        "ix_audit_findings_created_at",
        "audit_findings",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_findings_created_at", table_name="audit_findings")
    op.drop_index("ix_audit_findings_run_id", table_name="audit_findings")
    op.drop_table("audit_findings")
    op.drop_index("ix_audit_runs_created_at", table_name="audit_runs")
    op.drop_table("audit_runs")
