"""Deterministic postmortem / RCA draft from the AI session trail (v2 Phase 7).

Assembles a postmortem markdown draft from an incident's persisted session
``progress`` snapshots (observations / diagnosis / plan / workflow_result) plus
the incident lifecycle, mapped onto the canonical postmortem sections. Pure and
LLM-free — the operator reviews/edits the draft in the existing editor; nothing
is saved automatically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence


def _fmt_ts(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _plan_bullets(plan: Any) -> list[str]:
    bullets: list[str] = []
    if isinstance(plan, list):
        for step in plan:
            if isinstance(step, dict):
                text = (
                    step.get("description")
                    or step.get("action")
                    or step.get("tool")
                    or ""
                )
                tool = step.get("tool")
                if text and tool and tool not in text:
                    bullets.append(f"{text} (`{tool}`)")
                elif text:
                    bullets.append(str(text))
            elif step:
                bullets.append(str(step))
    return bullets


def draft_postmortem(incident: Any, sessions: Sequence[Any]) -> str:
    """Return a postmortem markdown draft assembled from the session trail."""
    # Prefer the most recently-started session that carries progress/summary.
    ordered = sorted(
        sessions, key=lambda s: getattr(s, "started_at", None) or datetime.min
    )
    latest_summary = next(
        (s.summary for s in reversed(ordered) if getattr(s, "summary", None)),
        None,
    )
    diagnosis = next(
        (
            (s.progress or {}).get("diagnosis")
            for s in reversed(ordered)
            if getattr(s, "progress", None) and (s.progress or {}).get("diagnosis")
        ),
        None,
    )
    observations = next(
        (
            (s.progress or {}).get("observations")
            for s in reversed(ordered)
            if getattr(s, "progress", None) and (s.progress or {}).get("observations")
        ),
        None,
    )
    plan_bullets: list[str] = []
    for s in reversed(ordered):
        plan_bullets = _plan_bullets((getattr(s, "progress", None) or {}).get("plan"))
        if plan_bullets:
            break

    created = getattr(incident, "created_at", None)
    resolved = getattr(incident, "resolved_at", None) or getattr(
        incident, "updated_at", None
    )
    priority = getattr(incident, "priority", None) or "—"

    timeline = [f"- {_fmt_ts(created)} — incident created"]
    for s in ordered:
        timeline.append(
            f"- {_fmt_ts(getattr(s, 'started_at', None))} — AI session started"
        )
        if getattr(s, "ended_at", None):
            timeline.append(f"- {_fmt_ts(s.ended_at)} — AI session ended ({s.status})")
    if getattr(incident, "status", None) == "resolved":
        timeline.append(f"- {_fmt_ts(resolved)} — resolved")

    summary_block = (
        latest_summary or "_Draft from the AI session trail — review and edit._"
    )
    impact_block = f"Priority {priority}. Created {_fmt_ts(created)}" + (
        f", resolved {_fmt_ts(resolved)}."
        if getattr(incident, "status", None) == "resolved"
        else ", ongoing."
    )
    if observations:
        impact_block += f"\n\nObserved signals:\n\n{observations}"
    root_cause_block = (
        diagnosis or "_No AI diagnosis was recorded — fill in the underlying cause._"
    )
    resolution_block = (
        "Proposed/taken actions from the AI session:\n\n"
        + "\n".join(f"- {b}" for b in plan_bullets)
        if plan_bullets
        else "_What was changed to mitigate — fill in._"
    )

    return "\n".join(
        [
            "## Summary",
            summary_block,
            "",
            "## Impact",
            impact_block,
            "",
            "## Timeline",
            *timeline,
            "",
            "## Root cause",
            root_cause_block,
            "",
            "## Resolution",
            resolution_block,
            "",
            "## Lessons learned",
            "_What worked, what didn't, what to change for next time._",
            "",
            "## Memory candidates",
            "_Short, durable lessons to save into OpsMender memory. One bullet per memory._",
            "",
        ]
    )
