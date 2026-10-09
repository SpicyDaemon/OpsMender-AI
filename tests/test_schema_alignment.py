"""M1-47 (R-23): the models describe the schema the migrations create.

`alembic check` on PostgreSQL (CI's Migrations job) compares the two in full;
these tests pin the parts that drifted, so a model change that reopens the
drift fails here too.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB

from backend.db.models import Base

JSONB_COLUMNS = [
    ("approval_requests", "action"),
    ("audit_entries", "tool_parameters"),
    ("audit_entries", "result"),
    ("ingest_log", "raw_payload"),
    ("ingest_tokens", "shape_cache"),
    ("maintenance_windows", "target_ids"),
    ("sla_targets", "config"),
    ("user_notification_prefs", "channels"),
    ("user_notification_prefs", "routing"),
    ("user_notification_prefs", "quiet_hours"),
    ("workflow_profiles", "node_order"),
]

INDEXES = {
    "ix_audit_entries_session_id": ("audit_entries", ["session_id"], False),
    "ix_audit_schedules_due": ("audit_schedules", ["is_active", "next_run_at"], False),
    "ix_incidents_external_fingerprint": (
        "incidents",
        ["external_source", "external_id"],
        False,
    ),
    "ix_ingest_log_token_id": ("ingest_log", ["ingest_token_id"], False),
    "ix_ingest_log_created_at": ("ingest_log", ["created_at"], False),
    "ix_org_invites_org_id": ("org_invites", ["org_id"], False),
    "ix_org_invites_email": ("org_invites", ["email"], False),
    "ix_password_reset_tokens_user_id": ("password_reset_tokens", ["user_id"], False),
    "ix_saml_assertion_replays_expires_at": (
        "saml_assertion_replays",
        ["expires_at"],
        False,
    ),
    "ix_services_intake_token_hash": ("services", ["intake_token_hash"], True),
    "ix_session_messages_session_id": ("session_messages", ["session_id"], False),
    "ix_sessions_org_model_config_status": (
        "sessions",
        ["org_id", "model_config_id", "status"],
        False,
    ),
    "ix_sessions_org_status_queued": (
        "sessions",
        ["org_id", "status", "queued_at"],
        False,
    ),
}

WORKSPACE_NAMES = {
    "ingest_tokens": "uq_ingest_token_name",
    "mcp_servers": "uq_mcp_server_name",
    "model_configs": "uq_model_config_name",
    "skills": "uq_skill_name",
    "sla_targets": "uq_sla_target_name",
    "workflow_profiles": "uq_workflow_profile_name",
}


@pytest.mark.parametrize(("table", "column"), JSONB_COLUMNS)
def test_these_columns_are_jsonb_on_postgres_and_json_elsewhere(table, column):
    kind = Base.metadata.tables[table].c[column].type
    assert isinstance(kind.dialect_impl(postgresql.dialect()), JSONB)
    assert not isinstance(kind.dialect_impl(sqlite.dialect()), JSONB)


@pytest.mark.parametrize("name", sorted(INDEXES))
def test_the_migration_indexes_are_declared(name):
    table, columns, unique = INDEXES[name]
    index = next(i for i in Base.metadata.tables[table].indexes if i.name == name)
    assert [column.name for column in index.columns] == columns
    assert index.unique is unique


def test_the_intake_token_hash_is_a_unique_index_not_a_constraint():
    services = Base.metadata.tables["services"]
    assert not services.c["intake_token_hash"].unique
    assert not any(
        isinstance(c, UniqueConstraint)
        and [col.name for col in c.columns] == ["intake_token_hash"]
        for c in services.constraints
    )


@pytest.mark.parametrize("table", sorted(WORKSPACE_NAMES))
def test_names_are_unique_per_workspace(table):
    constraints = {
        c.name: [col.name for col in c.columns]
        for c in Base.metadata.tables[table].constraints
        if isinstance(c, UniqueConstraint)
    }
    assert constraints[WORKSPACE_NAMES[table]] == ["org_id", "name"]
    assert not Base.metadata.tables[table].c["name"].unique
