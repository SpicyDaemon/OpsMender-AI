"""Session endpoints.

POST /sessions                      — start a new incident response session
GET  /sessions/{id}                 — get session details
GET  /sessions/{id}/messages        — list co-pilot chat messages
POST /sessions/{id}/messages        — append a user message + fire async reply
POST /sessions/{id}/rollback        — replay compensating inverses (Sprint 17)
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_current_session_factory, get_db, get_mcp_pool
from backend.api.routes.ws import publish
from backend.api.schemas import (
    RollbackStepResponse,
    SessionCreate,
    SessionMessageCreate,
    SessionMessageListResponse,
    SessionMessageResponse,
    SessionResponse,
    SessionRollbackRequest,
    SessionRollbackResponse,
    WSMessage,
)
from backend.audit.pg_logger import PgAuditLogger
from backend.chat import respond_to_user_message
from backend.config_loader import Config
from backend.db.models import User
from backend.db.repos import (
    AuditEntryRepo,
    IncidentRepo,
    MCPServerRepo,
    SessionMessageRepo,
    SessionRepo,
    SkillRepo,
)
from backend.mcp.client import list_tools as mcp_list_tools
from backend.mcp.pool import MCPServerPool
from backend.skills.parser import loads as load_skill_def
from backend.tiers.sandbox import Tier0Sandbox
from backend.workflow.rollback import (
    reconstruct_tool_calls,
    replay_compensating_inverses,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _tier0_max_session_seconds() -> int:
    try:
        return Config.load().tier0.max_session_seconds
    except (FileNotFoundError, ValueError):
        return 600


def _to_session_response(session) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        incident_id=session.incident_id,
        tier=session.tier,
        model_provider=session.model_provider,
        model_id=session.model_id,
        status=session.status,
        summary=session.summary,
        started_at=session.started_at,
        ended_at=session.ended_at,
        tier0_max_session_seconds=_tier0_max_session_seconds()
        if int(session.tier) == 0
        else None,
    )


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new session",
)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    # Validate linked incident exists (if provided)
    if body.incident_id is not None:
        incident = await IncidentRepo.get_by_id(db, body.incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Incident not found",
            )

    session = await SessionRepo.create(
        db,
        tier=body.tier,
        incident_id=body.incident_id,
        model_provider=body.model_provider,
        model_id=body.model_id,
    )

    briefing = (body.initial_briefing or "").strip()
    briefing_message_id: uuid.UUID | None = None
    if briefing:
        msg = await SessionMessageRepo.create(
            db,
            session_id=session.id,
            role="user",
            content=briefing,
            node_context="initial_briefing",
        )
        briefing_message_id = msg.id

    await db.commit()
    await db.refresh(session)

    # If a briefing was provided, fire the responder so the chat has an
    # assistant reply waiting by the time the UI connects.
    if briefing_message_id is not None:
        factory = get_current_session_factory()
        asyncio.create_task(
            respond_to_user_message(
                factory,
                session_id=session.id,
                user_message_id=briefing_message_id,
            )
        )

    return _to_session_response(session)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session details",
)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return _to_session_response(session)


# ---------------------------------------------------------------------------
# Co-pilot chat
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/messages",
    response_model=SessionMessageListResponse,
    summary="List co-pilot chat messages for a session",
)
async def list_session_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    items = await SessionMessageRepo.list_by_session(db, session_id)
    return SessionMessageListResponse(
        items=[SessionMessageResponse.model_validate(m) for m in items],
        total=len(items),
    )


@router.post(
    "/{session_id}/messages",
    response_model=SessionMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a user message to the co-pilot",
)
async def create_session_message(
    session_id: uuid.UUID,
    body: SessionMessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    message = await SessionMessageRepo.create(
        db,
        session_id=session_id,
        role="user",
        content=body.content,
    )
    await db.commit()
    await db.refresh(message)

    # Push the user event immediately so other connected clients see it.
    await publish(
        session_id,
        WSMessage(
            type="chat_message_user",
            data={
                "id": str(message.id),
                "session_id": str(session_id),
                "role": "user",
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "node_context": message.node_context,
            },
        ),
    )

    # Fire the assistant reply in the background — the route returns
    # immediately so the UI can optimistically render the user bubble.
    factory = get_current_session_factory()
    asyncio.create_task(
        respond_to_user_message(
            factory,
            session_id=session_id,
            user_message_id=message.id,
        )
    )

    return message
# ---------------------------------------------------------------------------
# Rollback (Sprint 17 — Tier 0 sandbox)
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/rollback",
    response_model=SessionRollbackResponse,
    summary="Roll back a session's executed operations (admin-only)",
)
async def rollback_session(
    session_id: uuid.UUID,
    body: SessionRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
    pool: MCPServerPool = Depends(get_mcp_pool),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if not body.dry_run and not body.mcp_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mcp_server is required unless dry_run is true",
        )

    entries = await AuditEntryRepo.list_by_session(db, session_id)
    tool_calls = reconstruct_tool_calls(entries)
    if not tool_calls:
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=body.dry_run,
            attempted=0,
            succeeded=0,
            failed=0,
            skipped=0,
            steps=[],
        )

    # -- Resolve the skill bound to the chosen MCP server --------------
    server_row = None
    if body.mcp_server:
        server_row = await MCPServerRepo.get_by_name(db, body.mcp_server)
        if server_row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MCP server not found: {body.mcp_server}",
            )
    skill_row = await SkillRepo.get_for_mcp_server(
        db, server_row.id if server_row else None
    )
    if skill_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No skill definition available — cannot resolve compensating inverses",
        )
    skill_def = load_skill_def(skill_row.content_md)

    # -- Dry-run: resolve inverses without calling MCP -----------------
    if body.dry_run:

        async def _noop(_tool, _params):
            return None

        report = await replay_compensating_inverses(
            session_id=str(session_id),
            tier=int(session.tier),
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=_noop,
            audit_logger=None,
        )
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=True,
            attempted=report.attempted,
            succeeded=report.succeeded,
            failed=report.failed,
            skipped=report.skipped,
            steps=[
                RollbackStepResponse(
                    original_tool=s.original_tool,
                    inverse_tool=s.inverse_tool,
                    parameters=s.parameters,
                    status=s.status,
                    error=s.error,
                )
                for s in report.steps
            ],
        )

    # -- Live rollback: spawn an MCP session + use the Tier 0 sandbox --
    logger = PgAuditLogger(db)
    async with pool.connect(body.mcp_server) as mcp_session:
        tools = await mcp_list_tools(mcp_session)
        sandbox = Tier0Sandbox.from_skill(skill_def, available_tools=tools)

        async def _caller(tool_name: str, params: dict):
            # Tier 0 sandbox guards even the rollback path.
            return await sandbox.call_tool(mcp_session, tool_name, params)

        report = await replay_compensating_inverses(
            session_id=str(session_id),
            tier=int(session.tier),
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=_caller,
            audit_logger=logger,
        )
    await db.commit()

    return SessionRollbackResponse(
        session_id=session_id,
        dry_run=False,
        attempted=report.attempted,
        succeeded=report.succeeded,
        failed=report.failed,
        skipped=report.skipped,
        steps=[
            RollbackStepResponse(
                original_tool=s.original_tool,
                inverse_tool=s.inverse_tool,
                parameters=s.parameters,
                status=s.status,
                error=s.error,
            )
            for s in report.steps
        ],
    )
