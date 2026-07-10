"""Async chat responder for the co-pilot channel."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.api.routes.ws import publish
from backend.api.schemas import WSMessage
from backend.bots.notifier import schedule_copilot_relay
from backend.db.models import SessionMessage
from backend.db.repos import (
    IncidentRepo,
    ModelConfigRepo,
    SessionMessageRepo,
    SessionRepo,
)
from backend.llm.base import LLM
from backend.llm.factory import create_llm

log = logging.getLogger(__name__)


class ChatResponderError(Exception):
    """Raised when the chat responder can't produce a reply."""


_SYSTEM_PROMPT = """You are the OpsMender AI co-pilot, running alongside \
an automated incident response workflow. Your job is to help the on-call \
engineer understand the incident, answer questions, and accept additional \
context they share. Be concise, specific, and grounded in the data provided.

You do NOT execute tools from this chat. Tool execution happens in the \
workflow under strict tier/skill enforcement. If the user asks you to run \
something destructive, explain that it goes through the workflow and any \
tier-1 actions require human approval.
"""


def _build_prompt(
    *,
    incident_block: str,
    session_block: str,
    history: list[SessionMessage],
    user_message: str,
) -> str:
    """Serialize state + transcript + latest user turn into a single prompt."""
    lines: list[str] = [
        _SYSTEM_PROMPT.strip(),
        "",
        "=== Incident ===",
        incident_block,
        "",
        "=== Session ===",
        session_block,
        "",
        "=== Conversation ===",
    ]
    for msg in history:
        lines.append(f"{msg.role.upper()}: {msg.content.strip()}")
    lines.append(f"USER: {user_message.strip()}")
    lines.append("ASSISTANT:")
    return "\n".join(lines)


def _incident_block(incident) -> str:
    if incident is None:
        return "(no incident linked to this session)"
    return (
        f"title: {incident.title}\n"
        f"status: {incident.status}\n"
        f"severity: {incident.severity or 'unspecified'}\n"
        f"description: {incident.description}"
    )


def _session_block(session) -> str:
    return (
        f"id: {session.id}\n"
        f"tier: {session.tier}\n"
        f"status: {session.status}\n"
        f"summary: {session.summary or '(none yet)'}"
    )


async def _resolve_llm(db, org_id: uuid.UUID, session) -> LLM:
    """Pick an LLM for the session: session-pinned model, then default, then stub."""
    if getattr(session, "model_config_id", None) is not None:
        pinned_cfg = await ModelConfigRepo.get_by_id(
            db, org_id, session.model_config_id
        )
        if pinned_cfg is not None:
            return create_llm(
                provider=pinned_cfg.provider,
                model_id=pinned_cfg.model_id,
                max_tokens=pinned_cfg.max_tokens,
                api_key_env_var=pinned_cfg.api_key_env_var,
                base_url=pinned_cfg.base_url,
                api_version=pinned_cfg.api_version,
                provider_meta=pinned_cfg.provider_meta,
            )
    provider = session.model_provider
    model_id = session.model_id

    if not provider:
        default_cfg = await ModelConfigRepo.get_default(db, org_id)
        if default_cfg is not None:
            return create_llm(
                provider=default_cfg.provider,
                model_id=default_cfg.model_id,
                max_tokens=default_cfg.max_tokens,
                api_key_env_var=default_cfg.api_key_env_var,
                base_url=default_cfg.base_url,
                api_version=default_cfg.api_version,
                provider_meta=default_cfg.provider_meta,
            )
        return create_llm(
            provider="stub",
            response="[co-pilot offline: no model configured]",
        )

    return create_llm(provider=provider, model_id=model_id)


async def respond_to_user_message(
    factory: async_sessionmaker,
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    user_message_id: uuid.UUID,
    llm_factory: Callable[[], LLM] | None = None,
) -> None:
    """Generate an assistant reply to the given user message."""
    try:
        async with factory() as db:
            session = await SessionRepo.get_by_id(db, org_id, session_id)
            if session is None:
                raise ChatResponderError(f"session {session_id} not found")

            incident = None
            if session.incident_id is not None:
                incident = await IncidentRepo.get_by_id(db, org_id, session.incident_id)

            history = list(
                await SessionMessageRepo.list_by_session(db, org_id, session_id)
            )
            history = [m for m in history if m.id != user_message_id]
            user_message = await SessionMessageRepo.get_by_id(
                db, org_id, user_message_id
            )
            if user_message is None:
                raise ChatResponderError(f"user message {user_message_id} not found")

            prompt = _build_prompt(
                incident_block=_incident_block(incident),
                session_block=_session_block(session),
                history=history,
                user_message=user_message.content,
            )

            llm = (
                llm_factory()
                if llm_factory
                else await _resolve_llm(db, org_id, session)
            )

        reply_text = await asyncio.to_thread(llm.invoke, prompt)
        reply_text = (reply_text or "").strip() or "[co-pilot returned no content]"

        async with factory() as db:
            assistant_msg = await SessionMessageRepo.create(
                db,
                org_id,
                session_id=session_id,
                role="assistant",
                content=reply_text,
            )
            await db.commit()
            await db.refresh(assistant_msg)

        await publish(
            session_id,
            WSMessage(
                type="chat_message_assistant",
                data={
                    "id": str(assistant_msg.id),
                    "session_id": str(session_id),
                    "role": "assistant",
                    "content": assistant_msg.content,
                    "created_at": assistant_msg.created_at.isoformat(),
                    "node_context": None,
                },
            ),
        )

        try:
            schedule_copilot_relay(
                factory,
                org_id=org_id,
                task_registry=None,
                session_id=session_id,
                reply_text=reply_text,
            )
        except Exception as relay_exc:  # noqa: BLE001
            log.debug(
                "copilot relay scheduling skipped for session %s: %s",
                session_id,
                relay_exc,
            )

    except Exception as exc:  # noqa: BLE001
        log.warning("chat responder failed for session %s: %s", session_id, exc)
        try:
            await publish(
                session_id,
                WSMessage(
                    type="error",
                    data={
                        "source": "copilot_chat",
                        "detail": str(exc),
                    },
                ),
            )
        except Exception:  # noqa: BLE001
            pass
