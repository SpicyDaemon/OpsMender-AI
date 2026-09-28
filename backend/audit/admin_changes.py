"""Audit authenticated configuration changes without copying secret values.

Row changes collect while a request's transaction is open. When it commits,
they become Activity entries inside that same transaction, so a change and
its entry are saved together or not at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AuditEntry

# These rows define workspace access, routing, integrations, and operator
# configuration. Runtime incident/session rows have their own audit trails.
_CONFIG_TABLES = frozenset(
    {
        "organizations",
        "organization_domains",
        "org_sso_configs",
        "org_saml_configs",
        "users",
        "org_invites",
        "user_organizations",
        "api_tokens",
        "model_configs",
        "mcp_servers",
        "mcp_server_oauth_tokens",
        "skills",
        "runtime_config",
        "org_email_settings",
        "org_voice_settings",
        "report_schedules",
        "integration_connectors",
        "workflow_profiles",
        "ingest_tokens",
        "incident_memories",
        "sla_targets",
        "slos",
        "maintenance_windows",
        "user_notification_prefs",
        "bot_connectors",
        "bot_user_links",
        "audit_schedules",
        "teams",
        "team_members",
        "services",
        "rosters",
        "roster_members",
        "roster_overrides",
        "service_rosters",
        "priority_rules",
        "escalation_chains",
        "escalation_steps",
        "service_escalation_chains",
        "retention_configs",
    }
)
# Enum-like values that stay readable. Booleans are always readable.
_SAFE_VALUES = frozenset({"role", "status", "priority", "tier", "step_index"})
_IGNORED_FIELDS = frozenset(
    {"id", "org_id", "created_at", "updated_at", "last_used_at", "last_login_at"}
)


def set_audit_actor(
    db: AsyncSession, request: Request, *, actor_id: uuid.UUID, org_id: uuid.UUID | None
) -> None:
    """Attach the successful HTTP identity to this request's DB session."""
    route = request.scope.get("route")
    db.sync_session.info["admin_audit"] = {
        "actor_id": str(actor_id),
        "org_id": str(org_id) if org_id else "",
        "route": getattr(route, "path", request.url.path),
        "method": request.method,
        "config_mutation": is_config_mutation(request),
        "entity_id": next(
            (
                str(value)
                for key, value in request.path_params.items()
                if key.endswith("_id")
            ),
            None,
        ),
        # Configuration rows changed in the open transaction, whether it
        # changed any other row, entries added to it, entries committed for
        # this request, and state saved when a savepoint began.
        "rows": {},
        "writes": False,
        "pending": 0,
        "entries": 0,
        "savepoints": {},
    }


_CONFIG_PREFIXES = (
    "/admin/",
    "/api/v1/api-tokens",
    "/api/v1/voice-settings",
    "/api/v1/workflow-settings",
    "/auth/me",
    "/auth/mfa",
    "/auth/users",
    "/audits/schedules",
    "/bot-connectors",
    "/config",
    "/escalation-chains",
    "/ingest-tokens",
    "/integrations",
    "/maintenance-windows",
    "/mcp-servers",
    "/memories",
    "/models/configs",
    "/organizations",
    "/priority-rules",
    "/reports/schedules",
    "/retention",
    "/rosters",
    "/services",
    "/skills",
    "/sla-targets",
    "/slos",
    "/teams",
    "/users/me/notification-preferences",
    "/notifications/preferences",
)


def is_config_mutation(request: Request) -> bool:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    path = request.url.path
    if not any(
        path == p.rstrip("/") or path.startswith(p if p.endswith("/") else p + "/")
        for p in _CONFIG_PREFIXES
    ):
        return False
    return not path.endswith(
        (
            "/test",
            "/validate",
            "/discover",
            "/generate",
            "/ai-suggest",
            "/probe",
            "/run",
        )
    )


def _summary(value: Any, field: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if field in _SAFE_VALUES and isinstance(value, (str, int)):
        return value
    if isinstance(value, (list, dict)):
        return {"count": len(value)}
    return "<set>"


def _same(before: Any, after: Any) -> bool:
    try:
        return bool(before == after)
    except Exception:  # SQL expressions have no truth value
        return False


def _row_facts(context: dict[str, Any], obj: Any) -> tuple[Any, Any, Any]:
    org_id = getattr(obj, "org_id", None) or (
        uuid.UUID(context["org_id"]) if context["org_id"] else None
    )
    entity_id = (
        getattr(obj, "id", None)
        or getattr(obj, "key", None)
        or getattr(obj, "user_id", None)
    )
    state = inspect(obj)
    # New rows get their identity only after the flush that inserted them.
    identity = state.identity or tuple(state.mapper.primary_key_from_instance(obj))
    return (obj.__table__.name, identity), org_id, entity_id


def _fold_row(
    context: dict[str, Any],
    facts: tuple[Any, Any, Any],
    operation: str,
    changes: dict[str, tuple[Any, Any]],
) -> None:
    """Merge one change into the open transaction's record of that row.

    A row changed in several steps, such as a two-phase reorder, becomes one
    entry from its first value to its last.
    """
    key, org_id, entity_id = facts
    if org_id is None:
        context["writes"] = True
        return
    row = context["rows"].get(key)
    if row is None:
        context["rows"][key] = {
            "org_id": org_id,
            "entity": key[0],
            "entity_id": entity_id,
            "operation": operation,
            "changes": dict(changes),
        }
        return
    if operation == "deleted":
        if row["operation"] == "created":
            del context["rows"][key]
            return
        row["operation"] = "deleted"
        row["changes"] = {
            field: (before, None) for field, (before, _) in row["changes"].items()
        }
    for field, (before, after) in changes.items():
        if field in row["changes"]:
            before = row["changes"][field][0]
        row["changes"][field] = (before, after)


def _admin_entry(
    context: dict[str, Any],
    org_id: uuid.UUID,
    entity: str,
    entity_id: Any,
    operation: str,
    fields: list[str],
    before: dict[str, Any],
    after: dict[str, Any],
) -> AuditEntry:
    return AuditEntry(
        org_id=org_id,
        session_id=None,
        tier=0,
        entry_type="admin_change",
        tool_name=f"{context['method']} {context['route']}",
        tool_parameters={
            "actor_id": context["actor_id"],
            "route": context["route"],
            "method": context["method"],
            "entity": entity,
            "entity_id": str(entity_id) if entity_id else None,
            "operation": operation,
        },
        result={"fields": fields, "before": before, "after": after},
    )


def _row_entry(context: dict[str, Any], row: dict[str, Any]) -> AuditEntry | None:
    operation, changes = row["operation"], row["changes"]
    if operation == "updated":
        changes = {field: pair for field, pair in changes.items() if not _same(*pair)}
        if not changes:
            return None
    fields = sorted(changes)[:12]
    if operation == "deleted" and not fields:
        before, after = {"state": "existing"}, {"state": "absent"}
    else:
        before = {field: _summary(changes[field][0], field) for field in fields}
        after = {field: _summary(changes[field][1], field) for field in fields}
    return _admin_entry(
        context,
        row["org_id"],
        row["entity"],
        row["entity_id"],
        operation,
        fields,
        before,
        after,
    )


def _route_entry(context: dict[str, Any]) -> AuditEntry:
    """Describe a configuration request whose rows no model hook covered."""
    operation = {
        "POST": "created",
        "PUT": "updated",
        "PATCH": "updated",
        "DELETE": "deleted",
    }[context["method"]]
    entity = next(
        (
            segment
            for segment in context["route"].split("/")
            if segment and segment not in {"admin", "api", "v1", "auth"}
        ),
        "configuration",
    )
    return _admin_entry(
        context,
        uuid.UUID(context["org_id"]),
        entity,
        context["entity_id"] or context["org_id"],
        operation,
        [],
        {"state": "absent" if operation == "created" else "existing"},
        {"state": "absent" if operation == "deleted" else "existing"},
    )


@event.listens_for(SyncSession, "do_orm_execute")
def _audit_direct_mutation(state):  # type: ignore[no-untyped-def]
    """Capture repository UPDATE/DELETE statements that bypass ORM history."""
    context = state.session.info.get("admin_audit")
    if not context or not (state.is_insert or state.is_update or state.is_delete):
        return None
    statement = state.statement
    entity = getattr(statement, "entity_description", {}).get("entity")
    values = getattr(statement, "_values", None) or {}
    if (
        state.is_insert
        or entity is None
        or getattr(entity, "__tablename__", None) not in _CONFIG_TABLES
        or (state.is_update and not values)
    ):
        context["writes"] = True
        return None
    updates = {
        str(getattr(key, "key", key)): getattr(value, "value", value)
        for key, value in values.items()
        if str(getattr(key, "key", key)) not in _IGNORED_FIELDS
    }
    if state.is_update and not updates:
        return None
    criteria = getattr(statement, "_where_criteria", ())
    prior = list(state.session.execute(select(entity).where(*criteria)).scalars().all())
    if not prior:
        return state.invoke_statement()
    snapshots = [
        (
            _row_facts(context, obj),
            {
                field: (getattr(obj, field, None), value)
                for field, value in updates.items()
            },
        )
        for obj in prior
    ]
    result = state.invoke_statement()
    if getattr(result, "rowcount", None) == 0:
        return result
    for facts, changes in snapshots:
        _fold_row(context, facts, "updated" if state.is_update else "deleted", changes)
    return result


@event.listens_for(SyncSession, "after_flush")
def _audit_config_flush(session: SyncSession, _flush_context: object) -> None:
    context = session.info.get("admin_audit")
    if not context:
        return
    for operation, objects in (
        ("created", session.new),
        ("updated", session.dirty),
        ("deleted", session.deleted),
    ):
        for obj in tuple(objects):
            if isinstance(obj, AuditEntry):
                continue
            if (
                not hasattr(obj, "__table__")
                or obj.__table__.name not in _CONFIG_TABLES
            ):
                if operation != "updated" or session.is_modified(obj):
                    context["writes"] = True
                continue
            state = inspect(obj)
            changes: dict[str, tuple[Any, Any]] = {}
            for column in state.mapper.column_attrs:
                name = column.key
                if name in _IGNORED_FIELDS:
                    continue
                history = state.attrs[name].history
                if operation == "updated" and not history.has_changes():
                    continue
                current = getattr(obj, name, None)
                before = history.deleted[0] if history.deleted else None
                if operation == "deleted":
                    before, current = current, None
                elif operation == "created":
                    before = None
                changes[name] = (before, current)
            if changes:
                _fold_row(context, _row_facts(context, obj), operation, changes)


@event.listens_for(SyncSession, "before_commit")
def _audit_commit(session: SyncSession) -> None:
    """Add this transaction's entries to it, so both commit or neither does."""
    context = session.info.get("admin_audit")
    if not context or session.in_nested_transaction():
        return
    if session.new or session.dirty or session.deleted:
        session.flush()
    for row in context["rows"].values():
        entry = _row_entry(context, row)
        if entry is not None:
            session.add(entry)
            context["pending"] += 1
    context["rows"] = {}
    if (
        context["config_mutation"]
        and context["writes"]
        and not context["entries"]
        and not context["pending"]
        and context["org_id"]
    ):
        session.add(_route_entry(context))
        context["pending"] += 1


@event.listens_for(SyncSession, "after_commit")
def _audit_committed(session: SyncSession) -> None:
    context = session.info.get("admin_audit")
    if context and not session.in_nested_transaction():
        context["entries"] += context["pending"]
        context.update(rows={}, writes=False, pending=0)


@event.listens_for(SyncSession, "after_rollback")
def _audit_rolled_back(session: SyncSession) -> None:
    context = session.info.get("admin_audit")
    if not context:
        return
    savepoint = session.get_nested_transaction()
    if savepoint is None:
        context.update(rows={}, writes=False, pending=0)
    elif id(savepoint) in context["savepoints"]:
        rows, writes = context["savepoints"][id(savepoint)]
        context.update(rows=rows, writes=writes)


@event.listens_for(SyncSession, "after_transaction_create")
def _audit_savepoint_begin(session: SyncSession, transaction: Any) -> None:
    context = session.info.get("admin_audit")
    if context and transaction.nested:
        context["savepoints"][id(transaction)] = (
            {
                key: {**row, "changes": dict(row["changes"])}
                for key, row in context["rows"].items()
            },
            context["writes"],
        )


@event.listens_for(SyncSession, "after_transaction_end")
def _audit_savepoint_end(session: SyncSession, transaction: Any) -> None:
    context = session.info.get("admin_audit")
    if context:
        context["savepoints"].pop(id(transaction), None)
