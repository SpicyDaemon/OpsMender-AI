"""Scope six names to their workspace, as the models do.

Ingest tokens, MCP servers, model configurations, skills, SLA targets and
workflow profiles had names unique across the whole database; the models
scope each name to its workspace (``org_id``, ``name``). Only the constraints
change, never a row (M1-47, R-23): every saved name is already unique, so the
workspace-scoped constraints hold.

PostgreSQL only: SQLite builds its schema from the models
(``backend/db/bootstrap.py``). Downgrade restores the global names, which
hold with one workspace per instance.

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
"""

from __future__ import annotations

from alembic import op

revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None

NAMES = (
    ("ingest_tokens", "uq_ingest_token_name"),
    ("mcp_servers", "uq_mcp_server_name"),
    ("model_configs", "uq_model_config_name"),
    ("skills", "uq_skill_name"),
    ("sla_targets", "uq_sla_target_name"),
    ("workflow_profiles", "uq_workflow_profile_name"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, constraint in NAMES:
        op.drop_constraint(f"{table}_name_key", table, type_="unique")
        op.create_unique_constraint(constraint, table, ["org_id", "name"])


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, constraint in NAMES:
        op.drop_constraint(constraint, table, type_="unique")
        op.create_unique_constraint(f"{table}_name_key", table, ["name"])
