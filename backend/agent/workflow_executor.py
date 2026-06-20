"""Execution engine for remediation workflows declared in SKILL.md."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import uuid
from typing import Any

from backend.approvals import ApprovalService
from backend.audit.executor import audited_tool_call
from backend.audit.logger import AuditEntryType
from backend.skills.parser import SkillDefinition, WorkflowStep
from backend.tiers.enforcement import EnforcementResult, check as tier_check


_TEMPLATE = re.compile(r"{{\s*([^{}]+?)\s*}}")


class WorkflowTemplateError(ValueError):
    """Raised when a workflow template references unavailable context."""


@dataclasses.dataclass
class WorkflowStepOutcome:
    step_id: str
    description: str
    tool: str
    inputs: dict[str, Any]
    status: str
    classification: str
    output: dict[str, Any] | None = None
    error: str | None = None
    block_reason: str | None = None
    approval_request_id: str | None = None
    effective_tier: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_tool_call_record(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool,
            "tool_parameters": self.inputs,
            "classification": self.classification,
            "permitted": self.status in {"completed", "failed"},
            "result": self.output,
            "error": self.error,
            "duration_ms": None,
            "block_reason": self.block_reason,
            "workflow_step_id": self.step_id,
            "workflow_step_status": self.status,
        }


@dataclasses.dataclass
class WorkflowResult:
    outcomes: list[WorkflowStepOutcome] = dataclasses.field(default_factory=list)
    aborted: bool = False
    abort_step_id: str | None = None

    @property
    def completed(self) -> bool:
        return not self.aborted and all(
            outcome.status != "failed" for outcome in self.outcomes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "aborted": self.aborted,
            "abort_step_id": self.abort_step_id,
            "completed": self.completed,
        }

    def tool_call_records(self) -> list[dict[str, Any]]:
        return [outcome.to_tool_call_record() for outcome in self.outcomes]


def _lookup_path(path: str, context: dict[str, Any]) -> Any:
    parts = [part for part in path.split(".") if part]
    if not parts or parts[0] not in {"incident", "steps"}:
        raise WorkflowTemplateError(
            f"Unsupported workflow template '{{{{{path}}}}}'"
        )
    value: Any = context
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        raise WorkflowTemplateError(
            f"Workflow template value '{{{{{path}}}}}' is unavailable"
        )
    return value


def resolve_templates(value: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve incident and prior-step template variables."""
    if isinstance(value, dict):
        return {key: resolve_templates(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, context) for item in value]
    if not isinstance(value, str):
        return value

    matches = list(_TEMPLATE.finditer(value))
    if not matches:
        return value
    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return _lookup_path(matches[0].group(1).strip(), context)

    def replace(match: re.Match[str]) -> str:
        resolved = _lookup_path(match.group(1).strip(), context)
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, sort_keys=True)
        return "" if resolved is None else str(resolved)

    return _TEMPLATE.sub(replace, value)


def _effective_enforcement(
    step: WorkflowStep,
    tier: int,
    skill_def: SkillDefinition,
) -> tuple[EnforcementResult, bool]:
    override = step.tier_override
    effective_tier = tier
    force_approval = override == "approval"
    if override in {"blocked", "advisory"}:
        base = tier_check(step.tool, tier, skill_def)
        return (
            dataclasses.replace(
                base,
                permitted=False,
                requires_approval=False,
                reason=f"workflow tier_override '{override}' blocks execution",
            ),
            False,
        )
    if override and override.startswith("T"):
        # A workflow may make a step safer, never more permissive than the
        # active session. In the 3-tier model, larger numbers are stricter.
        effective_tier = max(tier, int(override[1:]))
    enforcement = tier_check(step.tool, effective_tier, skill_def)
    return enforcement, force_approval and enforcement.permitted


class WorkflowExecutor:
    """Execute a Skill workflow in order with per-step tier enforcement."""

    def __init__(
        self,
        *,
        mcp_session: Any,
        skill_def: SkillDefinition,
        audit_logger: Any,
        tier: int,
        approval_service: ApprovalService | None = None,
        tool_caller=None,
    ) -> None:
        self._mcp_session = mcp_session
        self._skill_def = skill_def
        self._audit_logger = audit_logger
        self._tier = tier
        self._approval_service = approval_service
        self._tool_caller = tool_caller

    async def execute(
        self,
        *,
        session_id: str,
        incident: dict[str, Any] | None,
    ) -> WorkflowResult:
        result = WorkflowResult()
        context: dict[str, Any] = {
            "incident": incident or {},
            "steps": {},
        }

        for step in self._skill_def.workflow:
            try:
                inputs = resolve_templates(step.inputs, context)
            except WorkflowTemplateError as exc:
                outcome = WorkflowStepOutcome(
                    step_id=step.id,
                    description=step.description,
                    tool=step.tool,
                    inputs={},
                    status="failed",
                    classification=self._skill_def.classify(step.tool),
                    error=str(exc),
                )
                result.outcomes.append(outcome)
                await self._log_step(
                    AuditEntryType.WORKFLOW_STEP_FAILED,
                    session_id=session_id,
                    outcome=outcome,
                )
                # A template-resolution failure means the step could not even
                # start — an upstream dependency was skipped or failed. This
                # never aborts the workflow; `on_failure` governs tool-action
                # failures (handled after the tool call below), not unmet input
                # dependencies. The step is recorded failed and we move on.
                continue

            enforcement, force_approval = _effective_enforcement(
                step, self._tier, self._skill_def
            )
            effective_tier = enforcement.tier
            if not enforcement.permitted:
                outcome = WorkflowStepOutcome(
                    step_id=step.id,
                    description=step.description,
                    tool=step.tool,
                    inputs=inputs,
                    status="blocked",
                    classification=enforcement.classification,
                    block_reason=enforcement.reason,
                    effective_tier=effective_tier,
                )
                result.outcomes.append(outcome)
                await self._log_step(
                    AuditEntryType.WORKFLOW_STEP_BLOCKED,
                    session_id=session_id,
                    outcome=outcome,
                )
                continue

            approval_request_id: str | None = None
            if enforcement.requires_approval or force_approval:
                if self._approval_service is None:
                    outcome = WorkflowStepOutcome(
                        step_id=step.id,
                        description=step.description,
                        tool=step.tool,
                        inputs=inputs,
                        status="blocked",
                        classification=enforcement.classification,
                        block_reason="Workflow step requires an approval service",
                        effective_tier=effective_tier,
                    )
                    result.outcomes.append(outcome)
                    await self._log_step(
                        AuditEntryType.WORKFLOW_STEP_BLOCKED,
                        session_id=session_id,
                        outcome=outcome,
                    )
                    continue

                action = {
                    "workflow_step": True,
                    "step_id": step.id,
                    "description": step.description,
                    "tool_name": step.tool,
                    "tool_parameters": inputs,
                    "inputs": inputs,
                    "safety_class": enforcement.classification,
                    "tier_override": step.tier_override,
                }
                resolution = await self._approval_service.request_and_wait(
                    session_id=uuid.UUID(session_id),
                    action=action,
                    justification=step.description or None,
                )
                approval_request_id = str(resolution.request.id)
                if resolution.request.status != "approved":
                    guidance = (resolution.guidance or "").strip()
                    reason = resolution.block_reason or (
                        f"Workflow step approval {resolution.request.status}"
                    )
                    if guidance:
                        reason = f"{reason}: {guidance}"
                    outcome = WorkflowStepOutcome(
                        step_id=step.id,
                        description=step.description,
                        tool=step.tool,
                        inputs=inputs,
                        status="blocked",
                        classification=enforcement.classification,
                        block_reason=reason,
                        approval_request_id=approval_request_id,
                        effective_tier=effective_tier,
                    )
                    result.outcomes.append(outcome)
                    await self._log_step(
                        AuditEntryType.WORKFLOW_STEP_BLOCKED,
                        session_id=session_id,
                        outcome=outcome,
                    )
                    continue

            tool_result = await audited_tool_call(
                session=self._mcp_session,
                tool_name=step.tool,
                tool_parameters=inputs,
                session_id=session_id,
                tier=effective_tier,
                skill_def=self._skill_def,
                logger=self._audit_logger,
                tool_caller=self._tool_caller,
            )
            if tool_result.error:
                outcome = WorkflowStepOutcome(
                    step_id=step.id,
                    description=step.description,
                    tool=step.tool,
                    inputs=inputs,
                    status="failed",
                    classification=tool_result.enforcement.classification,
                    error=tool_result.error,
                    approval_request_id=approval_request_id,
                    effective_tier=effective_tier,
                )
                result.outcomes.append(outcome)
                await self._log_step(
                    AuditEntryType.WORKFLOW_STEP_FAILED,
                    session_id=session_id,
                    outcome=outcome,
                )
                if step.on_failure == "abort":
                    result.aborted = True
                    result.abort_step_id = step.id
                    break
                continue

            output = tool_result.result or {}
            outcome = WorkflowStepOutcome(
                step_id=step.id,
                description=step.description,
                tool=step.tool,
                inputs=inputs,
                status="completed",
                classification=tool_result.enforcement.classification,
                output=output,
                approval_request_id=approval_request_id,
                effective_tier=effective_tier,
            )
            result.outcomes.append(outcome)
            context["steps"][step.id] = {"output": output}
            await self._log_step(
                AuditEntryType.WORKFLOW_STEP_COMPLETED,
                session_id=session_id,
                outcome=outcome,
            )

        return result

    async def _log_step(
        self,
        entry_type: AuditEntryType,
        *,
        session_id: str,
        outcome: WorkflowStepOutcome,
    ) -> None:
        logger = getattr(self._audit_logger, "log_workflow_step", None)
        if logger is None:
            return
        pending = logger(
            session_id,
            outcome.effective_tier if outcome.effective_tier is not None else self._tier,
            entry_type,
            outcome.tool,
            tool_parameters={
                "step_id": outcome.step_id,
                "description": outcome.description,
                "inputs": outcome.inputs,
            },
            result=outcome.output or ({"error": outcome.error} if outcome.error else None),
            permitted=outcome.status != "blocked",
            block_reason=outcome.block_reason,
        )
        if inspect.isawaitable(pending):
            await pending
