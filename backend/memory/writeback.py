"""Sprint 45 Steps 4 + 5 — post-session memory writeback + auto-compaction.

Step 4 — `remember` node logic
------------------------------
After ``summarize`` runs on a successful workflow, we ask the LLM to distill
the session into one short lesson and persist it as an ``incident_memories``
row. The trigger criteria are intentionally conservative — see
:func:`should_remember`. Memory must never reward failed or rolled-back
sessions; the signal we keep is "this approach worked," not "this approach was
attempted."

The LLM response must be a strict JSON object matching ``MemoryDraft``. On any
parse failure the function logs and skips — memory is advisory, so a missing
write never blocks the session.

Step 5 — auto-compaction
------------------------
When an org passes :data:`COMPACTION_THRESHOLD` memories for a single service,
:func:`maybe_compact` runs one bounded dedup pass. Two layers:

1. **Exact-title dedup.** If two memories share an identical title (after
   normalisation), keep the newer one and delete the older. Pure SQL, no
   LLM call.
2. **LLM-driven near-duplicate dedup.** One LLM call lists candidate
   ``{action: "delete", ids: [...]}`` operations across the remaining
   memories. Applied transactionally, bounded to at most
   :data:`MAX_COMPACTION_OPS` per pass, never recursive, always audit-logged.

Compaction never runs on demand from an untrusted path. It runs at most once
per ``remember`` invocation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.agent.llm import LLM
from backend.db.models import IncidentMemory
from backend.db.repos import IncidentMemoryRepo
from backend.memory.tags import normalize_memory_tags

logger = logging.getLogger(__name__)

COMPACTION_THRESHOLD = 50

# v2 Phase 8 — bounded memory growth (opt-in). Off by default. When enabled and
# a service's memory count exceeds the configured ceiling, evict the
# lowest-value memories down to the ceiling. Operator-pinned and high-recall
# (helpful_count >= EVICTION_PROTECT_HELPFUL) memories are never evicted.
EVICTION_PROTECT_HELPFUL = 3
DEFAULT_EVICTION_MAX = 500


def _eviction_enabled() -> bool:
    return os.environ.get("OPSMENDER_MEMORY_EVICTION_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _eviction_max() -> int:
    raw = os.environ.get("OPSMENDER_MEMORY_MAX_PER_SERVICE", "").strip()
    if not raw:
        return DEFAULT_EVICTION_MAX
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_EVICTION_MAX


"""Per-service memory count above which auto-compaction runs after a write."""

MAX_COMPACTION_OPS = 5
"""Upper bound on deletes the LLM dedup pass can request per `remember` call."""

MEMORY_TITLE_MAX = 200
MEMORY_SUMMARY_MAX = 4000

REMEMBER_PROMPT = """\
You are reviewing a completed incident response session. Distill ONE short
lesson worth remembering for next time. Return ONLY a JSON object that matches
this exact schema, no prose around it:

{{
  "title": "<short title, max 100 chars, no quotes>",
  "tags": ["<lowercase tag>", ...],
  "summary_md": "<markdown summary, max 800 chars>"
}}

The summary should cover:
- What went wrong (symptoms)
- What turned out to be the cause
- What action resolved it
- Any gotchas to watch for next time

Include 1–5 lowercase hyphenated tags. Severity is a useful tag when present.

Session materials:

Incident: {incident_title}
Severity: {severity}

Observations:
{observations}

Diagnosis:
{diagnosis}

Actions taken ({tool_call_count} total, {blocked_count} blocked):
{actions}

Outcome summary:
{summary}

Return the JSON object only."""


COMPACTION_PROMPT = """\
You are reviewing accumulated incident-response memories for a single service.
Identify pairs that are near-duplicates — same root cause, same fix, same
symptoms — even if worded differently. Return ONLY a JSON array of operations,
no prose around it.

Each operation has the shape:

  {{"action": "delete", "id": "<memory-id>", "reason": "<one short sentence>"}}

Return at most {max_ops} operations. Prefer deleting older memories when in
doubt. If no duplicates exist, return an empty array [].

Candidate memories:

{candidates}

Return the JSON array only."""


@dataclass
class MemoryDraft:
    """Parsed LLM response for ``remember``."""

    title: str
    tags: list[str] = field(default_factory=list)
    summary_md: str = ""

    @classmethod
    def from_json(cls, raw: str) -> "MemoryDraft | None":
        """Parse and validate the LLM's JSON response.

        Returns ``None`` on any failure — caller logs and skips.
        """
        text = _strip_code_fence(raw).strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        title = str(data.get("title") or "").strip()
        if not title:
            return None
        if len(title) > MEMORY_TITLE_MAX:
            title = title[:MEMORY_TITLE_MAX].rstrip()
        summary = str(data.get("summary_md") or "").strip()
        if len(summary) > MEMORY_SUMMARY_MAX:
            summary = summary[:MEMORY_SUMMARY_MAX].rstrip()
        raw_tags = data.get("tags") or []
        tags = (
            normalize_memory_tags(raw_tags, limit=5)
            if isinstance(raw_tags, list)
            else []
        )
        return cls(title=title, tags=tags, summary_md=summary)


def _strip_code_fence(text: str) -> str:
    """Strip ```json ... ``` fences a model sometimes emits despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the first fence line and any closing fence.
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


def should_remember(state: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether the workflow earned a memory.

    Returns ``(ok, reason)`` so the caller can log the rationale for an
    audit trail.

    Criteria:
    - Workflow status is ``completed`` (didn't fail or time out).
    - No top-level ``error`` field is set.
    - No tool calls errored (block reasons are fine — a blocked Tier-1 action
      is a successful escalation, not a failure).
    - At least one of ``summary`` or ``diagnosis`` is non-trivial.
    """
    status = state.get("status")
    if status not in {"completed", None}:
        return False, f"workflow status was '{status}'"
    if state.get("error"):
        return False, "workflow error field was set"
    tool_calls = state.get("tool_calls") or []
    for tc in tool_calls:
        if isinstance(tc, dict) and tc.get("error"):
            return False, "at least one tool call errored"
    summary = (state.get("summary") or "").strip()
    diagnosis = (state.get("diagnosis") or "").strip()
    if len(summary) < 20 and len(diagnosis) < 20:
        return False, "summary and diagnosis are both empty/trivial"
    return True, "ok"


def _render_actions(state: dict[str, Any]) -> str:
    tool_calls = state.get("tool_calls") or []
    if not tool_calls:
        return "(none — advisory session)"
    lines: list[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("tool_name", "?")
        permitted = tc.get("permitted")
        suffix = " [executed]" if permitted else " [blocked]"
        lines.append(f"- {name}{suffix}")
    return "\n".join(lines) if lines else "(none)"


async def remember_for_session(
    factory: Any,
    *,
    llm: LLM | None,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
    source_incident_id: uuid.UUID | None,
    state: dict[str, Any],
) -> uuid.UUID | None:
    """Distill the session into a memory and persist it.

    Returns the new memory's id, or ``None`` if the session didn't earn a
    memory or the LLM response could not be parsed.
    """
    if factory is None or llm is None:
        return None

    ok, reason = should_remember(state)
    if not ok:
        logger.info("memory writeback skipped: %s", reason)
        return None

    incident = state.get("incident") or {}
    prompt = REMEMBER_PROMPT.format(
        incident_title=incident.get("title") or "(no title)",
        severity=incident.get("severity") or "(unknown)",
        observations=(state.get("observations") or "").strip()[:2000]
        or "(no observations)",
        diagnosis=(state.get("diagnosis") or "").strip()[:2000] or "(no diagnosis)",
        actions=_render_actions(state),
        tool_call_count=len(state.get("tool_calls") or []),
        blocked_count=len(state.get("blocked_actions") or []),
        summary=(state.get("summary") or "").strip()[:2000] or "(no summary)",
    )

    try:
        raw = llm.invoke(prompt)
    except Exception:
        logger.exception("memory writeback LLM call failed; skipping")
        return None

    draft = MemoryDraft.from_json(raw)
    if draft is None:
        logger.warning(
            "memory writeback could not parse LLM response; skipping. Raw: %s",
            raw[:200] if isinstance(raw, str) else type(raw).__name__,
        )
        return None

    try:
        async with factory() as db:
            memory = await IncidentMemoryRepo.create(
                db,
                org_id=org_id,
                service_id=service_id,
                source_incident_id=source_incident_id,
                title=draft.title,
                summary_md=draft.summary_md,
                tags=draft.tags,
            )
            await db.commit()
            new_id = memory.id
    except Exception:
        logger.exception("memory writeback DB write failed; skipping")
        return None

    # Auto-compaction is best-effort; failures must never propagate.
    try:
        await maybe_compact(factory, llm=llm, org_id=org_id, service_id=service_id)
    except Exception:
        logger.exception("memory auto-compaction failed; continuing")

    # Opt-in bounded-growth eviction (off by default). Also best-effort.
    try:
        await maybe_evict(factory, org_id=org_id, service_id=service_id)
    except Exception:
        logger.exception("memory eviction failed; continuing")

    return new_id


# ---------------------------------------------------------------------------
# Step 5 — auto-compaction
# ---------------------------------------------------------------------------


_TITLE_NORM = re.compile(r"\s+")


def _normalise_title(title: str) -> str:
    return _TITLE_NORM.sub(" ", title.strip().lower())


async def maybe_compact(
    factory: Any,
    *,
    llm: LLM | None,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
    threshold: int = COMPACTION_THRESHOLD,
) -> dict[str, Any]:
    """Run one bounded compaction pass for a single service.

    Returns a small report dict (``{exact_deleted, llm_deleted, total_after}``)
    so callers and tests can verify behaviour.
    """
    if factory is None:
        return {"exact_deleted": 0, "llm_deleted": 0, "total_after": 0}

    async with factory() as db:
        count = await IncidentMemoryRepo.count_for_service(db, org_id, service_id)
        if count <= threshold:
            return {
                "exact_deleted": 0,
                "llm_deleted": 0,
                "total_after": count,
            }

        memories = list(
            await IncidentMemoryRepo.list_for_org(
                db,
                org_id,
                service_id=service_id,
                global_only=service_id is None,
            )
        )

        # Pass 1 — exact title dedup. Keep the newer one.
        by_title: dict[str, IncidentMemory] = {}
        exact_deleted = 0
        for memory in memories:
            key = _normalise_title(memory.title)
            existing = by_title.get(key)
            if existing is None:
                by_title[key] = memory
                continue
            # Keep whichever is more recent; delete the older.
            if memory.created_at >= existing.created_at:
                older = existing
                by_title[key] = memory
            else:
                older = memory
            await IncidentMemoryRepo.delete(db, memory_id=older.id, org_id=org_id)
            exact_deleted += 1
        await db.commit()

        # Pass 2 — LLM near-duplicate dedup (bounded, optional).
        llm_deleted = 0
        if llm is not None:
            remaining = list(by_title.values())
            if len(remaining) > threshold and len(remaining) >= 2:
                llm_deleted = await _llm_compact(
                    db, llm=llm, memories=remaining, org_id=org_id
                )
                await db.commit()

        total_after = await IncidentMemoryRepo.count_for_service(
            db, org_id=org_id, service_id=service_id
        )

    return {
        "exact_deleted": exact_deleted,
        "llm_deleted": llm_deleted,
        "total_after": total_after,
    }


async def maybe_evict(
    factory: Any,
    *,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
    max_total: int | None = None,
    enabled: bool | None = None,
    protect_helpful: int = EVICTION_PROTECT_HELPFUL,
) -> dict[str, Any]:
    """Opt-in bounded-growth eviction for one service group (v2 Phase 8).

    Off by default. When enabled and the group exceeds ``max_total``, evict the
    least-valuable memories down to the ceiling — oldest by ``last_used_at``
    (falling back to ``created_at``) first. **Never** evicts pinned or
    high-recall (``helpful_count >= protect_helpful``) memories. Returns an
    observable report and logs each eviction. Best-effort; never raises.
    """
    enabled = _eviction_enabled() if enabled is None else enabled
    max_total = _eviction_max() if max_total is None else max_total
    report = {"evicted": 0, "protected": 0, "total_after": 0, "enabled": enabled}
    if not enabled or max_total <= 0 or factory is None:
        return report

    async with factory() as db:
        count = await IncidentMemoryRepo.count_for_service(db, org_id, service_id)
        if count <= max_total:
            report["total_after"] = count
            return report

        memories = list(
            await IncidentMemoryRepo.list_for_org(
                db, org_id, service_id=service_id, global_only=service_id is None
            )
        )
        evictable = [
            m
            for m in memories
            if not m.pinned and (m.helpful_count or 0) < protect_helpful
        ]
        report["protected"] = len(memories) - len(evictable)
        # Least-recently-used first; never-used sort by age.
        evictable.sort(key=lambda m: m.last_used_at or m.created_at)
        to_evict = count - max_total
        evicted = 0
        for memory in evictable[:to_evict]:
            await IncidentMemoryRepo.delete(db, memory_id=memory.id, org_id=org_id)
            evicted += 1
            logger.info(
                "memory.evicted org=%s service=%s memory=%s title=%r helpful=%s",
                org_id,
                service_id,
                memory.id,
                memory.title,
                memory.helpful_count,
            )
        await db.commit()
        report["evicted"] = evicted
        report["total_after"] = count - evicted
    return report


async def _llm_compact(
    db,
    *,
    llm: LLM,
    memories: list[IncidentMemory],
    org_id: uuid.UUID,
) -> int:
    """Ask the LLM for delete suggestions; apply up to MAX_COMPACTION_OPS."""
    candidates = "\n\n".join(
        f"id: {m.id}\ntitle: {m.title}\ntags: {', '.join(m.tags or [])}\n"
        f"summary: {(m.summary_md or '').strip()[:400]}"
        for m in memories
    )
    prompt = COMPACTION_PROMPT.format(max_ops=MAX_COMPACTION_OPS, candidates=candidates)
    try:
        raw = llm.invoke(prompt)
    except Exception:
        logger.exception("compaction LLM call failed")
        return 0

    text = _strip_code_fence(raw).strip()
    if not text:
        return 0
    try:
        ops = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("compaction parse failed; raw: %s", text[:200])
        return 0
    if not isinstance(ops, list):
        return 0

    deleted = 0
    valid_ids = {str(m.id) for m in memories}
    for op in ops[:MAX_COMPACTION_OPS]:
        if not isinstance(op, dict):
            continue
        if op.get("action") != "delete":
            continue
        target = op.get("id")
        if not isinstance(target, str) or target not in valid_ids:
            continue
        try:
            target_uuid = uuid.UUID(target)
        except ValueError:
            continue
        await IncidentMemoryRepo.delete(db, memory_id=target_uuid, org_id=org_id)
        deleted += 1
    return deleted
