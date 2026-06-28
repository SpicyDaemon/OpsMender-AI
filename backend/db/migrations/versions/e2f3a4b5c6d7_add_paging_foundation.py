"""Add paging foundation (Sprint 33).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-15 00:00:00.000000

Sprint 33 lays the foundation for OpsMender-owned paging (D-021):
teams, services, rosters with deterministic on-call resolution,
priority rules with optional LLM escalation log, and incident
assignments granting incident-scoped operator authority. The full
data model lives in ``docs/PROMPT_CONTEXT.md (D-021 — Paging Model)``.

Escalation chains, maintenance windows, notification preferences,
and incident_pages are deferred to Sprints 34–35.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Column additions on existing tables -----------------------------
    op.add_column(
        "organizations",
        sa.Column(
            "priority_llm_escalation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "incidents", sa.Column("priority", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "incidents",
        sa.Column("response_mode", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "incidents", sa.Column("service_id", sa.Uuid(), nullable=True)
    )
    # services table is created below, so the FK is added at the end.

    # ---- teams ----------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "slug", name="uq_team_slug"),
    )

    # ---- team_members ---------------------------------------------------
    op.create_table(
        "team_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default="member",
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_member"),
    )
    op.create_index(
        "ix_team_members_team_id", "team_members", ["team_id"], unique=False
    )

    # ---- services -------------------------------------------------------
    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("external_refs", sa.JSON(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "slug", name="uq_service_slug"),
    )
    op.create_index(
        "ix_services_team_id", "services", ["team_id"], unique=False
    )

    # incidents.service_id FK now that services exists.
    op.create_foreign_key(
        "fk_incidents_service_id",
        "incidents",
        "services",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ---- rosters --------------------------------------------------------
    op.create_table(
        "rosters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "time_zone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column(
            "pattern",
            sa.String(length=20),
            nullable=False,
            server_default="weekly",
        ),
        sa.Column(
            "pattern_length", sa.Integer(), nullable=False, server_default="7"
        ),
        sa.Column(
            "handoff_time",
            sa.String(length=8),
            nullable=False,
            server_default="09:00",
        ),
        sa.Column("handoff_day", sa.String(length=12), nullable=True),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rosters_team_id", "rosters", ["team_id"], unique=False
    )

    # ---- roster_members -------------------------------------------------
    op.create_table(
        "roster_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position_index", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["roster_id"], ["rosters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "roster_id", "user_id", name="uq_roster_member_user"
        ),
        sa.UniqueConstraint(
            "roster_id", "position_index", name="uq_roster_member_position"
        ),
    )
    op.create_index(
        "ix_roster_members_roster_id",
        "roster_members",
        ["roster_id"],
        unique=False,
    )

    # ---- roster_overrides -----------------------------------------------
    op.create_table(
        "roster_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("covering_user_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["roster_id"], ["rosters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["covering_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_roster_overrides_roster_id",
        "roster_overrides",
        ["roster_id"],
        unique=False,
    )

    # ---- service_rosters ------------------------------------------------
    op.create_table(
        "service_rosters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column(
            "level", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["service_id"], ["services.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["roster_id"], ["rosters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_id", "roster_id", name="uq_service_roster"
        ),
    )

    # ---- priority_rules -------------------------------------------------
    op.create_table(
        "priority_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "rule_index", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("response_mode", sa.String(length=30), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_priority_rules_org_index",
        "priority_rules",
        ["org_id", "rule_index"],
        unique=False,
    )

    # ---- priority_llm_override_log --------------------------------------
    op.create_table(
        "priority_llm_override_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("rule_priority", sa.String(length=8), nullable=False),
        sa.Column("llm_priority", sa.String(length=8), nullable=False),
        sa.Column("llm_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---- incident_assignments -------------------------------------------
    op.create_table(
        "incident_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.String(length=30), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_assignments_incident_id",
        "incident_assignments",
        ["incident_id"],
        unique=False,
    )
    # Unique partial index — only one active assignment per incident.
    op.create_index(
        "ix_incident_assignments_active",
        "incident_assignments",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_assignments_active", table_name="incident_assignments"
    )
    op.drop_index(
        "ix_incident_assignments_incident_id",
        table_name="incident_assignments",
    )
    op.drop_table("incident_assignments")
    op.drop_table("priority_llm_override_log")
    op.drop_index(
        "ix_priority_rules_org_index", table_name="priority_rules"
    )
    op.drop_table("priority_rules")
    op.drop_table("service_rosters")
    op.drop_index(
        "ix_roster_overrides_roster_id", table_name="roster_overrides"
    )
    op.drop_table("roster_overrides")
    op.drop_index(
        "ix_roster_members_roster_id", table_name="roster_members"
    )
    op.drop_table("roster_members")
    op.drop_index("ix_rosters_team_id", table_name="rosters")
    op.drop_table("rosters")
    op.drop_constraint(
        "fk_incidents_service_id", "incidents", type_="foreignkey"
    )
    op.drop_index("ix_services_team_id", table_name="services")
    op.drop_table("services")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_column("incidents", "service_id")
    op.drop_column("incidents", "response_mode")
    op.drop_column("incidents", "priority")
    op.drop_column("organizations", "priority_llm_escalation_enabled")
