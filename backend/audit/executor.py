"""Audited tool-call executor for Opsmender AI.

Wraps every MCP tool call with the required pre/post audit log entries
and tier enforcement check.  This is the single entry-point that the
LangGraph ``execute`` node (Sprint 5) will use.

Execution flow
--------------
1. **Pre-log** — ``tool_call_start`` audit entry
2. **Tier check** — classify the tool via the skill definition, then
   check against the active tier
3. If **blocked** → ``tool_call_blocked`` audit entry → return
4. If **permitted** → call the MCP tool → ``tool_call_end`` audit entry
   (includes result + duration)
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from mcp import ClientSession

from backend.audit.logger import AuditLogger
from backend.mcp.client import call_tool
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import EnforcementResult, check as tier_check


@dataclasses.dataclass
class AuditedToolResult:
    """Result returned by :func:`audited_tool_call`.

    Provides a unified view of what happened: was the call permitted,
    what the tier enforcement said, the MCP result (if executed), and
    how long it took.
    """

    permitted: bool
    enforcement: EnforcementResult
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None


async def audited_tool_call(
    *,
    session: ClientSession,
    tool_name: str,
    tool_parameters: dict[str, Any] | None = None,
    session_id: str,
    tier: int,
    skill_def: SkillDefinition,
    logger: AuditLogger,
    tool_caller: Callable[
        [ClientSession, str, dict[str, Any]],
        Awaitable[Any],
    ] | None = None,
) -> AuditedToolResult:
    """Execute an MCP tool call with full audit logging.

    This function implements the core enforcement contract described in
    REFERENCE.md: every tool call passes through tier enforcement,
    skill-definition check, audit log entry, then MCP execution.

    Parameters
    ----------
    session:
        Active MCP ``ClientSession``.
    tool_name:
        Name of the tool to invoke.
    tool_parameters:
        Arguments forwarded to the MCP tool.
    session_id:
        Incident session identifier (for the audit log).
    tier:
        Active tier (0–3).
    skill_def:
        Loaded skill definition used for classification.
    logger:
        Audit logger instance.
    tool_caller:
        Optional MCP invocation function. Defaults to
        :func:`backend.mcp.client.call_tool`. Tier 0 uses this hook to
        route execution through the sandbox allowlist.

    Returns
    -------
    AuditedToolResult
        Unified result with enforcement decision, MCP result, and
        timing information.
    """
    params = tool_parameters or {}

    # 1. Pre-log ─────────────────────────────────────────────────────
    logger.log_tool_call_start(session_id, tier, tool_name, params)

    # 2. Tier enforcement ────────────────────────────────────────────
    enforcement = tier_check(tool_name, tier, skill_def)

    if not enforcement.permitted:
        # 3a. Blocked ────────────────────────────────────────────────
        logger.log_tool_call_blocked(
            session_id,
            tier,
            tool_name,
            tool_parameters=params,
            block_reason=enforcement.reason,
        )
        return AuditedToolResult(
            permitted=False,
            enforcement=enforcement,
        )

    # 3b. Permitted — execute ────────────────────────────────────────
    start = time.monotonic()
    try:
        caller = tool_caller or call_tool
        mcp_result = await caller(session, tool_name, params)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        result_dict = {
            "content": [
                {"type": c.type, "text": getattr(c, "text", "")}
                for c in (mcp_result.content or [])
            ],
            "isError": mcp_result.isError,
        }

        # 4. Post-log ───────────────────────────────────────────────
        logger.log_tool_call_end(
            session_id,
            tier,
            tool_name,
            result=result_dict,
            duration_ms=elapsed_ms,
        )

        return AuditedToolResult(
            permitted=True,
            enforcement=enforcement,
            result=result_dict,
            duration_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        error_str = str(exc)

        logger.log_tool_call_end(
            session_id,
            tier,
            tool_name,
            result={"error": error_str},
            duration_ms=elapsed_ms,
        )

        return AuditedToolResult(
            permitted=True,
            enforcement=enforcement,
            error=error_str,
            duration_ms=elapsed_ms,
        )
