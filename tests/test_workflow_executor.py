"""Tests for Skill-defined remediation workflow execution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from backend.agent.nodes import _build_workflow_plan
from backend.agent.workflow_executor import WorkflowExecutor
from backend.approvals.service import ApprovalResolution
from backend.audit.logger import AuditEntryType, AuditLogger
from backend.skills.convert import convert_legacy_skill_content
from backend.skills.parser import loads


SESSION_ID = str(uuid.uuid4())


def _skill(*, first_failure: str = "abort", approval: bool = False):
    override = "\n    tier_override: approval" if approval else ""
    return loads(
        convert_legacy_skill_content(
            f"""---
version: "1"
environment: test
operations:
  - tool: find_pod
    classification: safe
  - tool: restart_pod
    classification: safe
---
## Workflow
```yaml
steps:
  - id: find
    description: Find the failing pod
    tool: find_pod
    inputs:
      incident_id: "{{{{incident.id}}}}"
    on_failure: {first_failure}{override}
  - id: restart
    description: Restart the pod
    tool: restart_pod
    inputs:
      pod: "{{{{steps.find.output.pod}}}}"
      title: "Incident: {{{{incident.title}}}}"
    on_failure: abort
```
"""
        ).content
    )


class _ApprovalService:
    def __init__(self, status: str = "approved") -> None:
        self.status = status
        self.actions: list[dict] = []

    async def request_and_wait(self, *, session_id, action, justification=None):
        self.actions.append(action)
        return ApprovalResolution(
            request=SimpleNamespace(id=uuid.uuid4(), status=self.status),
            block_reason=None if self.status == "approved" else "Approval rejected",
        )


async def test_executes_steps_in_order_with_template_substitution(tmp_path):
    calls: list[tuple[str, dict]] = []

    async def caller(_session, tool_name, params):
        calls.append((tool_name, params))
        if tool_name == "find_pod":
            return {"pod": "api-7"}
        return {"restarted": params["pod"]}

    logger = AuditLogger(tmp_path / "audit.jsonl")
    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=_skill(),
        audit_logger=logger,
        tier=0,
        tool_caller=caller,
    )
    result = await executor.execute(
        session_id=SESSION_ID,
        incident={"id": "inc-123", "title": "API down"},
    )

    assert calls == [
        ("find_pod", {"incident_id": "inc-123"}),
        ("restart_pod", {"pod": "api-7", "title": "Incident: API down"}),
    ]
    assert [item.status for item in result.outcomes] == ["completed", "completed"]
    assert result.outcomes[1].output == {"restarted": "api-7"}
    assert [
        entry.entry_type
        for entry in logger.read_all()
        if entry.entry_type.value.startswith("workflow.step")
    ] == [
        AuditEntryType.WORKFLOW_STEP_COMPLETED,
        AuditEntryType.WORKFLOW_STEP_COMPLETED,
    ]


async def test_tier_override_approval_forces_approval_in_tier_zero(tmp_path):
    approval = _ApprovalService()
    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        return {"pod": "api-7"} if tool_name == "find_pod" else {"ok": True}

    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=_skill(approval=True),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tier=0,
        approval_service=approval,
        tool_caller=caller,
    )
    result = await executor.execute(
        session_id=SESSION_ID,
        incident={"id": "inc-123", "title": "API down"},
    )

    assert result.outcomes[0].status == "completed"
    assert calls == ["find_pod", "restart_pod"]
    assert approval.actions == [
        {
            "workflow_step": True,
            "step_id": "find",
            "description": "Find the failing pod",
            "tool_name": "find_pod",
            "tool_parameters": {"incident_id": "inc-123"},
            "inputs": {"incident_id": "inc-123"},
            "safety_class": "safe",
            "tier_override": "approval",
        }
    ]


@pytest.mark.parametrize(
    ("on_failure", "expected_calls", "aborted"),
    [
        ("abort", ["find_pod"], True),
        ("continue", ["find_pod"], False),
    ],
)
async def test_failure_abort_and_continue(
    tmp_path, on_failure, expected_calls, aborted
):
    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        if tool_name == "find_pod":
            raise RuntimeError("lookup failed")
        return {"ok": True}

    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=_skill(first_failure=on_failure),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tier=0,
        tool_caller=caller,
    )
    result = await executor.execute(
        session_id=SESSION_ID,
        incident={"id": "inc-123", "title": "API down"},
    )

    # A continued workflow reaches step two, but its reference to the failed
    # first step cannot resolve and fails before invoking the tool.
    assert calls == expected_calls
    assert result.aborted is aborted
    if on_failure == "abort":
        assert len(result.outcomes) == 1
        assert result.abort_step_id == "find"
    else:
        assert [item.status for item in result.outcomes] == ["failed", "failed"]


async def test_step_output_and_error_are_redacted_before_they_are_recorded(
    tmp_path,
):
    """M1-98: a step's output and error are redacted in the session state the
    way the execute node redacts its tool results, while a later step still
    resolves its template from the real output."""
    password = "password=" + "s" * 12
    token = "ghp_" + "t" * 24
    calls: list[tuple[str, dict]] = []

    async def caller(_session, tool_name, params):
        calls.append((tool_name, params))
        if tool_name == "find_pod":
            return {"pod": "api-7", "note": f"config loaded, {password}"}
        raise RuntimeError(f"registry refused {token}")

    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=_skill(),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tier=0,
        tool_caller=caller,
    )
    workflow_plan = _build_workflow_plan(executor)

    state = await workflow_plan(
        {
            "session_id": SESSION_ID,
            "incident": {"id": "inc-9", "title": f"API down {token}"},
        }
    )

    # The second step got the real pod name and title through its template.
    assert calls[1] == (
        "restart_pod",
        {"pod": "api-7", "title": f"Incident: API down {token}"},
    )
    first, second = state["tool_calls"]
    assert first["result"] == {
        "pod": "api-7",
        "note": "config loaded, password=[redacted secret]",
    }
    assert second["error"] == "registry refused [redacted secret]"
    # A template that copied the secret into the second step's inputs does not
    # put it in the record either.
    assert second["tool_parameters"] == {
        "pod": "api-7",
        "title": "Incident: API down [redacted secret]",
    }
    outcomes = state["workflow_result"]["outcomes"]
    assert outcomes[0]["output"]["note"] == "config loaded, password=[redacted secret]"
    assert outcomes[1]["error"] == "registry refused [redacted secret]"
    assert outcomes[1]["inputs"]["title"] == "Incident: API down [redacted secret]"
    recorded = repr(state)
    assert "s" * 12 not in recorded and token not in recorded
