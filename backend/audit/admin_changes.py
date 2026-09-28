"""Audit authenticated configuration changes without copying secret values."""

from __future__ import annotations

import uuid
from datetime import datetime
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
_SAFE_VALUES = frozenset(
    {"role", "status", "priority", "tier", "is_active", "enabled", "step_index"}
)
_IGNORED_FIELDS = frozenset(
    {"id", "org_id", "created_at", "updated_at", "last_used_at", "last_login_at"}
)


def set_audit_actor(
    db: AsyncSession, request: Request, *, actor_id: uuid.UUID, org_id: uuid.UUID | None
) -> None:
    """Attach the successful HTTP identity to this request's DB session."""
    route = request.scope.get("route")
    context = {
        "actor_id": str(actor_id),
        "org_id": str(org_id) if org_id else "",
        "route": getattr(route, "path", request.url.path),
        "method": request.method,
        "entries": 0,
    }
    db.sync_session.info["admin_audit"] = context
    request.state.admin_audit = context


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


async def audit_uncaptured_change(request: Request, *, status_code: int) -> None:
    """Cover successful direct SQL mutations that bypass ORM flush history."""
    context = getattr(request.state, "admin_audit", None)
    if (
        not context
        or context["entries"]
        or status_code >= 400
        or not is_config_mutation(request)
    ):
        return
    org_id = context.get("org_id")
    if not org_id:
        return
    path_params = request.path_params
    entity_id = (
        next(
            (str(value) for key, value in path_params.items() if key.endswith("_id")),
            None,
        )
        or org_id
    )
    entity = next(
        (
            segment
            for segment in context["route"].split("/")
            if segment and segment not in {"admin", "api", "v1", "auth"}
        ),
        "configuration",
    )
    operation = {
        "POST": "created",
        "PUT": "updated",
        "PATCH": "updated",
        "DELETE": "deleted",
    }[request.method]
    before_state = "absent" if operation == "created" else "existing"
    after_state = "absent" if operation == "deleted" else "existing"
    from backend.api.deps import get_current_session_factory

    async with get_current_session_factory()() as db:
        db.add(
            AuditEntry(
                org_id=uuid.UUID(org_id),
                session_id=None,
                tier=0,
                entry_type="admin_change",
                tool_name=f"{context['method']} {context['route']}",
                tool_parameters={
                    "actor_id": context["actor_id"],
                    "route": context["route"],
                    "method": context["method"],
                    "entity": entity,
                    "entity_id": entity_id,
                    "operation": operation,
                },
                result={
                    "fields": [],
                    "before": {"state": before_state},
                    "after": {"state": after_state},
                },
            )
        )
        await db.commit()


def _summary(value: Any, field: str) -> Any:
    if value is None:
        return None
    if field in _SAFE_VALUES and isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (list, dict)):
        return {"count": len(value)}
    if isinstance(value, (uuid.UUID, datetime)):
        return "<set>"
    return "<set>"


@event.listens_for(SyncSession, "do_orm_execute")
def _audit_direct_mutation(state):  # type: ignore[no-untyped-def]
    """Capture repository UPDATE/DELETE statements that bypass ORM history."""
    context = state.session.info.get("admin_audit")
    if not context or not (state.is_update or state.is_delete):
        return None
    statement = state.statement
    entity = getattr(statement, "entity_description", {}).get("entity")
    if entity is None or getattr(entity, "__tablename__", None) not in _CONFIG_TABLES:
        return None
    criteria = getattr(statement, "_where_criteria", ())
    prior = list(state.session.execute(select(entity).where(*criteria)).scalars().all())
    if not prior:
        return state.invoke_statement()
    values = getattr(statement, "_values", {}) if state.is_update else {}
    fields = sorted(str(getattr(key, "key", key)) for key in values)[:12]
    updates = {
        str(getattr(key, "key", key)): getattr(value, "value", value)
        for key, value in values.items()
    }
    snapshots = [
        (
            getattr(obj, "org_id", None)
            or (uuid.UUID(context["org_id"]) if context["org_id"] else None),
            getattr(obj, "id", None) or getattr(obj, "key", None),
            {field: _summary(getattr(obj, field, None), field) for field in fields},
        )
        for obj in prior
    ]
    result = state.invoke_statement()
    if getattr(result, "rowcount", None) == 0:
        return result
    for org_id, entity_id, before in snapshots:
        if org_id is None:
            continue
        state.session.add(
            AuditEntry(
                org_id=org_id,
                session_id=None,
                tier=0,
                entry_type="admin_change",
                tool_name=f"{context['method']} {context['route']}",
                tool_parameters={
                    "actor_id": context["actor_id"],
                    "route": context["route"],
                    "method": context["method"],
                    "entity": entity.__tablename__,
                    "entity_id": str(entity_id) if entity_id else None,
                    "operation": "updated" if state.is_update else "deleted",
                },
                result={
                    "fields": fields,
                    "before": before if state.is_update else {"state": "existing"},
                    "after": (
                        {field: _summary(updates.get(field), field) for field in fields}
                        if state.is_update
                        else {"state": "absent"}
                    ),
                },
            )
        )
        context["entries"] += 1
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
            if (
                not hasattr(obj, "__table__")
                or obj.__table__.name not in _CONFIG_TABLES
            ):
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
                changes[name] = (_summary(before, name), _summary(current, name))
            if not changes:
                continue
            org_id = getattr(obj, "org_id", None) or (
                uuid.UUID(context["org_id"]) if context["org_id"] else None
            )
            if org_id is None:
                continue
            entity_id = (
                getattr(obj, "id", None)
                or getattr(obj, "key", None)
                or getattr(obj, "user_id", None)
            )
            fields = sorted(changes)[:12]
            session.add(
                AuditEntry(
                    org_id=org_id,
                    session_id=None,
                    tier=0,
                    entry_type="admin_change",
                    tool_name=f"{context['method']} {context['route']}",
                    tool_parameters={
                        "actor_id": context["actor_id"],
                        "route": context["route"],
                        "method": context["method"],
                        "entity": obj.__table__.name,
                        "entity_id": str(entity_id) if entity_id else None,
                        "operation": operation,
                    },
                    result={
                        "fields": fields,
                        "before": {key: changes[key][0] for key in fields},
                        "after": {key: changes[key][1] for key in fields},
                    },
                )
            )
            context["entries"] += 1
