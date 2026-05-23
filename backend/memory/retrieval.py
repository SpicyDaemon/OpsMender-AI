"""Sprint 45 Step 2 — session-aware memory retrieval.

Wraps :class:`backend.db.repos.IncidentMemoryRepo` with the side effects the
``recall`` LangGraph node needs:

- look up the top-K memories for the active org + service + incident text,
- write a row to ``incident_memory_recall_log`` per surfaced memory,
- bump ``last_used_at`` on each one,
- return both a markdown context block ready for prompt injection and the
  list of surfaced memory ids so the rest of the workflow can reference
  them (e.g. for the "Memories used" panel on the session detail page).

Memory is **advisory only**. This module never gates execution; it never
raises into the agent loop. On any failure the caller gets an empty
result back and the session keeps running.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.db.models import IncidentMemory
from backend.db.repos import (
    IncidentMemoryRecallLogRepo,
    IncidentMemoryRepo,
)

logger = logging.getLogger(__name__)

# A few short, content-free stop words we strip from the incident title before
# treating it as a keyword query. Lower-cased; matched after `.split()`.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "its",
        "from",
        "by",
    }
)


@dataclass
class MemoryRetrievalResult:
    """Output of :func:`recall_for_session`."""

    memories: list[tuple[IncidentMemory, float]] = field(default_factory=list)
    context_block: str = ""
    memory_ids: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.memories


def derive_query(incident: dict[str, Any] | None) -> str | None:
    """Pick a short, keyword-shaped query from an incident dict.

    We use the incident title — it's short, operator-authored when manual, and
    alert-system-authored when ingested, so it's almost always the best
    signal. Description gets noisy fast for ingested incidents (raw JSON
    payloads); we keep it out of the query.
    """
    if not incident:
        return None
    title = (incident.get("title") or "").strip()
    if not title:
        return None
    # Strip punctuation that won't appear in a memory title anyway, lower-case
    # the result, drop stop words, drop tokens shorter than 3 chars.
    cleaned = re.sub(r"[^\w\s-]", " ", title).lower()
    tokens = [t for t in cleaned.split() if len(t) >= 3 and t not in _STOP_WORDS]
    return " ".join(tokens) if tokens else None


def derive_tags(incident: dict[str, Any] | None) -> list[str]:
    """Derive lightweight tags from the incident metadata.

    Severity becomes a tag; status does not (every fresh incident is "open").
    """
    if not incident:
        return []
    tags: list[str] = []
    severity = incident.get("severity")
    if isinstance(severity, str) and severity:
        tags.append(severity.lower())
    return tags


def format_memories_as_markdown(
    memories: list[tuple[IncidentMemory, float]],
) -> str:
    """Render surfaced memories as a markdown block ready for prompt injection.

    The block is wrapped with a clear heading so the LLM understands these are
    *prior lessons* rather than the current incident. Each lesson keeps its
    title, tags, and short summary; long memories get truncated to 1200 chars
    so the prompt stays bounded.
    """
    if not memories:
        return ""

    lines: list[str] = [
        "### Past lessons from similar incidents",
        "",
        (
            "The following lessons were distilled from previous incidents on this "
            "service. They are advisory context only — do not skip your own "
            "diagnosis, but use them to inform what you check first."
        ),
        "",
    ]

    for memory, _score in memories:
        tags = ", ".join(str(t) for t in (memory.tags or []))
        summary = memory.summary_md or ""
        if len(summary) > 1200:
            summary = summary[:1200].rstrip() + "…"
        lines.append(f"#### {memory.title}")
        if tags:
            lines.append(f"_tags: {tags}_")
        lines.append("")
        lines.append(summary)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def recall_for_session(
    factory: Any,
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    service_id: uuid.UUID | None,
    incident: dict[str, Any] | None,
    limit: int = 5,
) -> MemoryRetrievalResult:
    """Look up + record memories relevant to the active session.

    Parameters
    ----------
    factory:
        Async session factory (``async_sessionmaker``). When ``None``, the
        function returns an empty result — useful for CLI / test paths that
        run the graph without a DB.
    org_id, session_id:
        Active session's owning org and id. ``session_id`` must already exist
        in ``sessions`` so the ``incident_memory_recall_log`` FK can attach.
    service_id:
        Active incident's owning service. May be ``None`` for unbound
        incidents — global memories will still surface.
    incident:
        Same shape as ``IncidentContext`` in :mod:`backend.agent.state`.
    limit:
        Top-K memories to surface. Default 5 keeps the prompt bounded.
    """
    if factory is None:
        return MemoryRetrievalResult()

    query = derive_query(incident)
    tags = derive_tags(incident)

    try:
        async with factory() as db:
            ranked = await IncidentMemoryRepo.find_relevant(
                db,
                org_id=org_id,
                service_id=service_id,
                query=query,
                tags=tags,
                limit=limit,
            )
            if not ranked:
                return MemoryRetrievalResult()

            memory_ids: list[str] = []
            for memory, score in ranked:
                await IncidentMemoryRecallLogRepo.record(
                    db,
                    memory_id=memory.id,
                    session_id=session_id,
                    score=float(score),
                )
                await IncidentMemoryRepo.touch_last_used(db, memory.id)
                memory_ids.append(str(memory.id))
            await db.commit()
    except Exception:  # pragma: no cover — memory must never break a session
        logger.exception(
            "memory recall failed for session %s; continuing without memory",
            session_id,
        )
        return MemoryRetrievalResult()

    return MemoryRetrievalResult(
        memories=ranked,
        context_block=format_memories_as_markdown(ranked),
        memory_ids=memory_ids,
    )
