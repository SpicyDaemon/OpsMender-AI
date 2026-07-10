"""Tier 0 rollback engine (Sprint 17, rollback pillar).

Given a session's recorded tool-call history, replay each tool's
compensating inverse in reverse order.  Every attempt — success, skip
(no inverse declared), or failure — is recorded via the existing audit
logger so the trail is preserved.

Design notes
------------

* **Only successful, permitted tool calls are candidates for rollback.**
  A call that was blocked at the tier gate or errored out never took
  effect, so there is nothing to undo.
* **Reverse order is load-bearing.**  If a session cordoned a node then
  drained it, we need to uncordon *after* the drain is reversed, not
  before.
* **Best effort.**  One failed inverse does not abort the rest — the
  remaining inverses still run.  The returned :class:`RollbackReport`
  tells the caller exactly what happened.
* **Parametric inverses are a known limitation.**  We pass the original
  call's parameters to the inverse tool unchanged.  Some inverses (e.g.
  scaling up vs. down, committing vs. reverting a patch) would need
  parameter rewriting.  Skill authors should declare such ops without a
  ``compensating_inverse`` so the rollback engine skips them cleanly.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Protocol

from backend.skills.parser import SkillDefinition


class _ToolCaller(Protocol):
    """Minimal protocol for the MCP tool-call callable.

    The engine accepts any awaitable ``call(tool_name, params) -> Any``.
    Production usage passes ``functools.partial(call_tool, mcp_session)``
    or a lambda; tests can inject an ``AsyncMock``.
    """

    async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


@dataclasses.dataclass
class RollbackStep:
    """One line-item in the rollback report."""

    original_tool: str
    inverse_tool: str | None
    parameters: dict[str, Any]
    status: (
        str  # "succeeded" | "failed" | "skipped_no_inverse" | "skipped_not_permitted"
    )
    error: str | None = None


@dataclasses.dataclass
class RollbackReport:
    """Aggregate result of a rollback replay."""

    steps: list[RollbackStep]

    @property
    def attempted(self) -> int:
        return sum(1 for s in self.steps if s.status in ("succeeded", "failed"))

    @property
    def succeeded(self) -> int:
        return sum(1 for s in self.steps if s.status == "succeeded")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for s in self.steps if s.status.startswith("skipped_"))


def _is_rollback_candidate(record: dict[str, Any]) -> bool:
    """A tool-call record is rolled back only if it was permitted AND ran clean."""
    return bool(record.get("permitted")) and not record.get("error")


def _entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def reconstruct_tool_calls(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """Rebuild successful tool calls from an audit trail.

    Accepts either ORM rows / dataclass entries with attributes or plain
    dicts. A rollback candidate is a ``tool_call_start`` entry with
    ``permitted=True`` that has a matching ``tool_call_end`` later in the
    same session history.
    """
    ends_by_tool: dict[str, int] = {}
    entries_list = list(entries)
    for entry in entries_list:
        entry_type = _entry_value(entry, "entry_type")
        tool_name = _entry_value(entry, "tool_name")
        if hasattr(entry_type, "value"):
            entry_type = entry_type.value
        if entry_type == "tool_call_end" and tool_name:
            ends_by_tool[str(tool_name)] = ends_by_tool.get(str(tool_name), 0) + 1

    tool_calls: list[dict[str, Any]] = []
    for entry in entries_list:
        entry_type = _entry_value(entry, "entry_type")
        tool_name = _entry_value(entry, "tool_name")
        if hasattr(entry_type, "value"):
            entry_type = entry_type.value
        if entry_type != "tool_call_start" or not tool_name:
            continue
        if not bool(_entry_value(entry, "permitted")):
            continue
        tool_name = str(tool_name)
        if ends_by_tool.get(tool_name, 0) <= 0:
            continue
        ends_by_tool[tool_name] -= 1
        tool_calls.append(
            {
                "tool_name": tool_name,
                "tool_parameters": dict(_entry_value(entry, "tool_parameters") or {}),
                "permitted": True,
                "error": None,
            }
        )
    return tool_calls


async def replay_compensating_inverses(
    *,
    session_id: str,
    tier: int,
    tool_calls: list[dict[str, Any]],
    skill_def: SkillDefinition,
    caller: _ToolCaller,
    audit_logger: Any | None = None,
) -> RollbackReport:
    """Replay compensating inverses for *tool_calls* in reverse order.

    Parameters
    ----------
    session_id, tier:
        Used only for audit logging so the rollback trail links back to
        the owning session.
    tool_calls:
        The session's ``tool_calls`` list — the same schema produced by
        the execute node (see :class:`backend.agent.state.ToolCallRecord`).
    skill_def:
        Source of truth for the ``compensating_inverse`` annotation.
    caller:
        Awaitable that performs the MCP tool call.  The engine does not
        import the MCP client directly so tests can inject a mock.
    audit_logger:
        Optional — a ``PgAuditLogger`` or JSONL ``AuditLogger``.  When
        provided, each rollback attempt logs a ``tool_call_start`` /
        ``tool_call_end`` pair (or ``tool_call_blocked`` for skips).  The
        ``tool_parameters`` dict carries a reserved ``_rollback_of`` key
        naming the original tool so queries can distinguish the replay.
    """
    steps: list[RollbackStep] = []

    for record in reversed(tool_calls):
        original_tool = str(record.get("tool_name", ""))
        params = dict(record.get("tool_parameters") or {})

        if not _is_rollback_candidate(record):
            steps.append(
                RollbackStep(
                    original_tool=original_tool,
                    inverse_tool=None,
                    parameters=params,
                    status="skipped_not_permitted",
                )
            )
            continue

        inverse = skill_def.inverse_for(original_tool)
        if inverse is None:
            steps.append(
                RollbackStep(
                    original_tool=original_tool,
                    inverse_tool=None,
                    parameters=params,
                    status="skipped_no_inverse",
                )
            )
            await _log_skip(audit_logger, session_id, tier, original_tool, params)
            continue

        await _log_attempt_start(
            audit_logger, session_id, tier, inverse, original_tool, params
        )
        try:
            await caller(inverse, params)
        except Exception as exc:
            steps.append(
                RollbackStep(
                    original_tool=original_tool,
                    inverse_tool=inverse,
                    parameters=params,
                    status="failed",
                    error=str(exc),
                )
            )
            await _log_attempt_blocked(
                audit_logger, session_id, tier, inverse, original_tool, str(exc)
            )
            continue

        steps.append(
            RollbackStep(
                original_tool=original_tool,
                inverse_tool=inverse,
                parameters=params,
                status="succeeded",
            )
        )
        await _log_attempt_end(audit_logger, session_id, tier, inverse, original_tool)

    return RollbackReport(steps=steps)


# ---------------------------------------------------------------------------
# Audit wiring helpers — accept sync or async loggers, shrug off failures
# ---------------------------------------------------------------------------


async def _call_logger(logger: Any, method_name: str, **kwargs: Any) -> None:
    """Invoke ``logger.method_name(**kwargs)`` whether sync or async.

    Best-effort — a broken audit logger must not abort a rollback.
    """
    if logger is None:
        return
    method = getattr(logger, method_name, None)
    if method is None:
        return
    try:
        result = method(**kwargs)
        if hasattr(result, "__await__"):
            await result
    except Exception:  # noqa: BLE001
        pass


async def _log_attempt_start(logger, session_id, tier, inverse, original, params):
    await _call_logger(
        logger,
        "log_tool_call_start",
        session_id=session_id,
        tier=tier,
        tool_name=inverse,
        tool_parameters={**params, "_rollback_of": original},
    )


async def _log_attempt_end(logger, session_id, tier, inverse, original):
    await _call_logger(
        logger,
        "log_tool_call_end",
        session_id=session_id,
        tier=tier,
        tool_name=inverse,
        result={"rollback_of": original, "outcome": "succeeded"},
    )


async def _log_attempt_blocked(logger, session_id, tier, inverse, original, reason):
    await _call_logger(
        logger,
        "log_tool_call_blocked",
        session_id=session_id,
        tier=tier,
        tool_name=inverse,
        tool_parameters={"_rollback_of": original},
        block_reason=f"rollback failed: {reason}",
    )


async def _log_skip(logger, session_id, tier, original, params):
    await _call_logger(
        logger,
        "log_tool_call_blocked",
        session_id=session_id,
        tier=tier,
        tool_name=original,
        tool_parameters={**params, "_rollback_of": original},
        block_reason="rollback skipped: no compensating inverse declared",
    )
