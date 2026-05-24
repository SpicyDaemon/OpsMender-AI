"""API-driven session workflow runner.

Turns a created DB session row into a live workflow execution task:

* resolves the incident, model, MCP server, and skill context
* builds the LangGraph workflow
* streams node/tool/approval/session events over WebSocket
* persists audit + session status updates
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import pathlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from backend.agent.graph import build_graph
from backend.agent.timeouts import Tier0TimeConfig, ainvoke_with_session_timeout
from backend.api.routes.ws import publish
from backend.api.schemas import WSMessage
from backend.approvals import ApprovalService
from backend.audit.logger import AuditEntryType
from backend.config_loader import AppConfig, MCPServerConfig
from backend.db.models import Session as SessionModel
from backend.db.repos import (
    AgentTeamProfileRepo,
    AuditEntryRepo,
    IncidentRepo,
    ModelConfigRepo,
    SessionMessageRepo,
    SessionRepo,
    SkillRepo,
    WorkflowProfileRepo,
)
from backend.llm.base import LLM
from backend.llm.factory import create_llm
from backend.mcp.pool import MCPServerPool
from backend.skills.parser import SkillDefinition, load as load_skill_def, loads as load_skill_def_text
from backend.tiers.sandbox import build_sandbox_for_session
from backend.bots.notifier import schedule_session_chat_event
from backend.webhooks import schedule_session_event
from backend.workflow.rollback import reconstruct_tool_calls, replay_compensating_inverses

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _publish_node_event(
    session_id: uuid.UUID,
    node_name: str,
    status: str,
) -> None:
    await publish(
        session_id,
        WSMessage(
            type="node_transition",
            data={"node": node_name, "status": status},
        ),
    )


class LiveAuditLogger:
    """DB-backed audit logger that also emits live WebSocket events."""

    def __init__(
        self,
        factory,
        *,
        org_id: uuid.UUID,
        session_id: uuid.UUID,
        publisher: Callable[[uuid.UUID, WSMessage], Awaitable[None]] = publish,
    ) -> None:
        self._factory = factory
        self._org_id = org_id
        self._session_id = session_id
        self._publisher = publisher

    async def log_tool_call_start(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
    ) -> str:
        async with self._factory() as db:
            entry = await AuditEntryRepo.create(
                db,
                org_id=self._org_id,
                session_id=uuid.UUID(session_id),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_START.value,
                tool_name=tool_name,
                tool_parameters=tool_parameters,
                permitted=True,
            )
            await db.commit()
        await self._publisher(
            self._session_id,
            WSMessage(
                type="tool_call",
                data={
                    "tool_name": tool_name,
                    "parameters": tool_parameters or {},
                    "permitted": True,
                    "phase": "start",
                },
            ),
        )
        return str(entry.id)

    async def log_tool_call_end(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        result: dict | None = None,
        duration_ms: int | None = None,
    ) -> str:
        async with self._factory() as db:
            entry = await AuditEntryRepo.create(
                db,
                org_id=self._org_id,
                session_id=uuid.UUID(session_id),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_END.value,
                tool_name=tool_name,
                result=result,
                permitted=True,
                duration_ms=duration_ms,
            )
            await db.commit()
        await self._publisher(
            self._session_id,
            WSMessage(
                type="tool_call",
                data={
                    "tool_name": tool_name,
                    "permitted": True,
                    "phase": "end",
                    "result": result or {},
                    "duration_ms": duration_ms,
                },
            ),
        )
        return str(entry.id)

    async def log_tool_call_blocked(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
        block_reason: str | None = None,
    ) -> str:
        async with self._factory() as db:
            entry = await AuditEntryRepo.create(
                db,
                org_id=self._org_id,
                session_id=uuid.UUID(session_id),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_BLOCKED.value,
                tool_name=tool_name,
                tool_parameters=tool_parameters,
                permitted=False,
                block_reason=block_reason,
            )
            await db.commit()
        await self._publisher(
            self._session_id,
            WSMessage(
                type="tool_call",
                data={
                    "tool_name": tool_name,
                    "parameters": tool_parameters or {},
                    "permitted": False,
                    "block_reason": block_reason,
                    "phase": "blocked",
                },
            ),
        )
        return str(entry.id)

    async def log_session_start(self, session_id: str, tier: int) -> str:
        async with self._factory() as db:
            entry = await AuditEntryRepo.create(
                db,
                org_id=self._org_id,
                session_id=uuid.UUID(session_id),
                tier=tier,
                entry_type=AuditEntryType.SESSION_START.value,
                permitted=True,
            )
            await db.commit()
        return str(entry.id)

    async def log_session_end(self, session_id: str, tier: int) -> str:
        async with self._factory() as db:
            entry = await AuditEntryRepo.create(
                db,
                org_id=self._org_id,
                session_id=uuid.UUID(session_id),
                tier=tier,
                entry_type=AuditEntryType.SESSION_END.value,
                permitted=True,
            )
            await db.commit()
        return str(entry.id)

    async def read_by_session(self, session_id: str):
        async with self._factory() as db:
            return await AuditEntryRepo.list_by_session(db, self._org_id, uuid.UUID(session_id))


async def _await_maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _resolve_llm(factory, session) -> LLM:  # type: ignore[no-untyped-def]
    async with factory() as db:
        if not session.model_provider:
            default_cfg = await ModelConfigRepo.get_default(db, session.org_id)
            if default_cfg is not None:
                return create_llm(
                    provider=default_cfg.provider,
                    model_id=default_cfg.model_id,
                    max_tokens=default_cfg.max_tokens,
                    api_key_env_var=default_cfg.api_key_env_var,
                    base_url=default_cfg.base_url,
                    api_version=default_cfg.api_version,
                )
            return create_llm(
                provider="stub",
                response="[workflow offline: no model configured]",
            )

    return create_llm(
        provider=session.model_provider,
        model_id=session.model_id,
    )


async def _resolve_incident_and_messages(factory, session):  # type: ignore[no-untyped-def]
    async with factory() as db:
        incident = None
        if session.incident_id is not None:
            incident = await IncidentRepo.get_by_id(db, session.org_id, session.incident_id)
        pending_messages = list(
            await SessionMessageRepo.list_pending_user(db, session.org_id, session.id)
        )
        return incident, pending_messages


async def _mark_messages_consumed(factory, org_id: uuid.UUID, session_id: uuid.UUID, node_context: str) -> None:
    async with factory() as db:
        await SessionMessageRepo.mark_consumed(
            db, org_id, session_id, node_context=node_context
        )
        await db.commit()


async def _resolve_mcp_context(
    factory,
    org_id: uuid.UUID,
    pool: MCPServerPool,
    config: AppConfig,
) -> tuple[MCPServerConfig | None, SkillDefinition]:
    servers = await pool.list_servers(active_only=True)
    selected_server = servers[0] if servers else None

    async with factory() as db:
        server_id = None
        if selected_server is not None:
            from backend.db.repos import MCPServerRepo

            server_row = await MCPServerRepo.get_by_name(db, org_id, selected_server.name)
            server_id = None if server_row is None else server_row.id
        skill_row = await SkillRepo.get_for_mcp_server(db, org_id, server_id)

    if skill_row is not None:
        return selected_server, load_skill_def_text(skill_row.content_md)

    skill_path = pathlib.Path(config.app.skill_definition_path)
    if skill_path.is_file():
        return selected_server, load_skill_def(skill_path)
    return selected_server, load_skill_def("examples/SKILL.md")


def _build_incident_description(
    incident,
    pending_messages,
) -> str:  # type: ignore[no-untyped-def]
    lines: list[str] = []
    if incident is not None:
        lines.append(incident.description)
    if pending_messages:
        lines.append("")
        lines.append("Operator context:")
        for msg in pending_messages:
            lines.append(f"- {msg.content.strip()}")
    return "\n".join(line for line in lines if line is not None).strip()


def _incident_payload(incident) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if incident is None:
        return {
            "title": "",
            "description": "",
            "status": "open",
            "severity": None,
        }
    return {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "status": incident.status,
        "severity": incident.severity,
    }


async def _set_session_terminal_state(
    factory,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    status: str,
    summary: str | None = None,
) -> None:
    async with factory() as db:
        await SessionRepo.set_status(
            db,
            org_id,
            session_id,
            status=status,
            summary=summary,
            ended_at=_utcnow(),
        )
        await db.commit()


async def _session_snapshot(factory, session_id: uuid.UUID):
    async with factory() as db:
        # Load session globally to resolve its organization context
        return await db.get(SessionModel, session_id)


async def _auto_rollback_tier0(
    *,
    factory,
    org_id: uuid.UUID,
    pool: MCPServerPool,
    session_id: uuid.UUID,
    session_tier: int,
    server_name: str | None,
    skill_def: SkillDefinition,
    audit_logger: LiveAuditLogger,
) -> dict[str, Any] | None:
    if session_tier != 0 or server_name is None:
        return None

    entries = await audit_logger.read_by_session(str(session_id))
    tool_calls = reconstruct_tool_calls(entries)
    if not tool_calls:
        return None

    async with pool.connect(server_name) as mcp_session:
        sandbox = await build_sandbox_for_session(mcp_session, skill_def)
        report = await replay_compensating_inverses(
            session_id=str(session_id),
            tier=session_tier,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=lambda tool_name, params: sandbox.call_tool(
                mcp_session, tool_name, params
            ),
            audit_logger=audit_logger,
        )
    return {
        "attempted": report.attempted,
        "succeeded": report.succeeded,
        "failed": report.failed,
        "skipped": report.skipped,
    }


async def run_session_workflow(
    app: FastAPI,
    *,
    session_id: uuid.UUID,
) -> None:
    """Execute one session workflow in the background."""

    config: AppConfig = app.state.config
    factory = app.state.session_factory
    pool: MCPServerPool = app.state.mcp_pool

    startup_delay = getattr(app.state, "workflow_start_delay_seconds", 0.15)
    if startup_delay > 0:
        await asyncio.sleep(startup_delay)

    try:
        session = await _session_snapshot(factory, session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")
        
        org_id = session.org_id

        llm = await _resolve_llm(factory, session)
        incident, pending_messages = await _resolve_incident_and_messages(factory, session)
        if pending_messages:
            await _mark_messages_consumed(factory, org_id, session_id, "workflow_start")

        selected_server, skill_def = await _resolve_mcp_context(factory, org_id, pool, config)
        audit_logger = LiveAuditLogger(factory, org_id=org_id, session_id=session_id)
        approval_service = ApprovalService(
            factory,
            org_id=org_id,
            timeout_seconds=config.approvals.timeout_seconds,
            publisher=lambda sid, event: publish(sid, WSMessage(**event)),
            status_notifier=lambda sid, status: (
                schedule_session_event(
                    factory,
                    org_id=org_id,
                    task_registry=app.state.background_tasks,
                    event_type=f"session.{status}",
                    session_id=sid,
                ),
                schedule_session_chat_event(
                    factory,
                    org_id=org_id,
                    task_registry=app.state.background_tasks,
                    event_type=f"session.{status}",
                    session_id=sid,
                ),
            ),
        )

        incident_description = _build_incident_description(incident, pending_messages)
        initial_state = {
            "session_id": str(session_id),
            "tier": int(session.tier),
            "incident_description": incident_description,
            "incident": _incident_payload(incident),
            "message_history": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                    "node_context": msg.node_context,
                }
                for msg in pending_messages
            ],
            "pending_user_messages": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                    "node_context": msg.node_context,
                }
                for msg in pending_messages
            ],
        }

        graph_kwargs: dict[str, Any] = {
            "tier": int(session.tier),
            "skill_def": skill_def,
            "llm": llm,
            "approval_service": approval_service,
            "node_event_publisher": lambda node_name, status, _payload=None: _publish_node_event(
                session_id, node_name, status
            ),
            # Sprint 45 — feed memory into the graph so the recall node can
            # query org + service scope. service_id may be None for unbound
            # incidents; recall handles that case (global memories still
            # surface).
            "memory_factory": factory,
            "org_id": org_id,
            "service_id": getattr(incident, "service_id", None),
            "source_incident_id": getattr(incident, "id", None),
        }
        if session.workflow_profile_id is not None:
            async with factory() as db:
                workflow_profile = await WorkflowProfileRepo.get_by_id(
                    db, org_id, session.workflow_profile_id
                )
            if workflow_profile is not None:
                graph_kwargs["node_order"] = list(workflow_profile.node_order or [])
        if getattr(session, "agent_team_profile_id", None) is not None:
            async with factory() as db:
                agent_team_profile = await AgentTeamProfileRepo.get_by_id(
                    db, org_id, session.agent_team_profile_id
                )
            if agent_team_profile is not None:
                graph_kwargs["agent_roles"] = list(agent_team_profile.roles or [])

        server_name = None if selected_server is None else selected_server.name
        if int(session.tier) == 0:
            graph_kwargs["tier0_time_config"] = Tier0TimeConfig(
                max_session_seconds=config.tier0.max_session_seconds,
                max_node_seconds=config.tier0.max_node_seconds,
            )

        if selected_server is not None:
            async with pool.connect(selected_server.name) as mcp_session:
                server_name = selected_server.name
                if int(session.tier) == 0:
                    sandbox = await build_sandbox_for_session(mcp_session, skill_def)
                    graph_kwargs["plan_tool_names"] = sorted(sandbox.allowed_tool_names)
                    graph_kwargs["tool_caller"] = sandbox.call_tool
                graph_kwargs["mcp_session"] = mcp_session
                graph_kwargs["audit_logger"] = audit_logger

                graph = build_graph(**graph_kwargs)
                await _await_maybe(
                    audit_logger.log_session_start(str(session_id), int(session.tier))
                )
                if int(session.tier) == 0:
                    result = await ainvoke_with_session_timeout(
                        graph,
                        initial_state,
                        seconds=config.tier0.max_session_seconds,
                    )
                else:
                    result = await graph.ainvoke(initial_state)
        else:
            graph = build_graph(**graph_kwargs)
            await _await_maybe(
                audit_logger.log_session_start(str(session_id), int(session.tier))
            )
            if int(session.tier) == 0:
                result = await ainvoke_with_session_timeout(
                    graph,
                    initial_state,
                    seconds=config.tier0.max_session_seconds,
                )
            else:
                result = await graph.ainvoke(initial_state)

        rollback_summary = None
        final_status = result.get("status", "completed")
        if final_status in {"failed", "timed_out"}:
            rollback_summary = await _auto_rollback_tier0(
                factory=factory,
                org_id=org_id,
                pool=pool,
                session_id=session_id,
                session_tier=int(session.tier),
                server_name=server_name,
                skill_def=skill_def,
                audit_logger=audit_logger,
            )
            if rollback_summary is not None:
                result["rollback"] = rollback_summary

        await _set_session_terminal_state(
            factory,
            org_id,
            session_id,
            status=final_status,
            summary=result.get("summary"),
        )
        schedule_session_event(
            factory,
            org_id=org_id,
            task_registry=app.state.background_tasks,
            event_type=f"session.{final_status}",
            session_id=session_id,
        )
        schedule_session_chat_event(
            factory,
            org_id=org_id,
            task_registry=app.state.background_tasks,
            event_type=f"session.{final_status}",
            session_id=session_id,
        )
        await _await_maybe(
            audit_logger.log_session_end(str(session_id), int(session.tier))
        )
        await publish(
            session_id,
            WSMessage(
                type="session_end",
                data={
                    "status": final_status,
                    "summary": result.get("summary"),
                    "rollback": rollback_summary,
                },
            ),
        )

    except Exception as exc:  # noqa: BLE001
        log.exception("session workflow failed for %s", session_id)
        # Try to resolve org_id from session if we haven't yet
        try:
            session = await _session_snapshot(factory, session_id)
            org_id = session.org_id if session else None
        except Exception:
            org_id = None
            
        if org_id:
            await _set_session_terminal_state(
                factory,
                org_id,
                session_id,
                status="failed",
                summary=f"Workflow failed: {exc}",
            )
            schedule_session_event(
                factory,
                org_id=org_id,
                task_registry=app.state.background_tasks,
                event_type="session.failed",
                session_id=session_id,
            )
            schedule_session_chat_event(
                factory,
                org_id=org_id,
                task_registry=app.state.background_tasks,
                event_type="session.failed",
                session_id=session_id,
            )
        await publish(
            session_id,
            WSMessage(
                type="error",
                data={"source": "workflow", "detail": str(exc)},
            ),
        )
        await publish(
            session_id,
            WSMessage(
                type="session_end",
                data={"status": "failed", "summary": f"Workflow failed: {exc}"},
            ),
        )


def schedule_session_workflow(app: FastAPI, *, session_id: uuid.UUID) -> asyncio.Task:
    """Schedule the background task and track it on app state."""
    task = asyncio.create_task(run_session_workflow(app, session_id=session_id))
    tasks: set[asyncio.Task] = app.state.session_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task
