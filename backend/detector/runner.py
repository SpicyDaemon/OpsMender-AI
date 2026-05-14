"""Detector runner for MCP-driven incident detection.

Runs a single detector rule against one MCP server by:
1. Discovering available MCP tools
2. Building a read-only skill profile for observation-only tools
3. Asking the configured LLM to pick a short observation plan
4. Executing the safe observation tools
5. Asking the LLM for a structured incident verdict
6. Deduplicating or creating an OpsMender incident if an issue was detected
7. Recording run history in ``detector_history``
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.types import Tool
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.db.models import DetectorRule, Incident
from backend.db.repos import DetectorHistoryRepo, DetectorRuleRepo, IncidentRepo, ModelConfigRepo
from backend.llm.factory import create_provider
from backend.mcp.client import call_tool, list_tools
from backend.mcp.pool import MCPServerPool
from backend.skills.parser import OperationClassification, SkillDefinition

READ_ONLY_PREFIXES = (
    "get_",
    "list_",
    "describe_",
    "read_",
    "fetch_",
    "query_",
    "search_",
    "show_",
    "inspect_",
    "status_",
    "tail_",
)

PLAN_PROMPT = """\
You are an SRE detector operating against an MCP server named "{server_name}".

Goal:
{goal}

Available read-only MCP tools:
{tool_lines}

Choose at most 3 observation steps that would help determine whether there is
an actionable incident right now. Return ONLY a JSON array like:
[
  {{"tool_name": "get_pods", "tool_parameters": {{"namespace": "default"}}, "justification": "Check pod health"}}
]

Rules:
- Use only the listed tools
- Read-only observations only
- Be conservative and concise
- If no tool calls are needed, return []
"""

VERDICT_PROMPT = """\
You are an SRE detector deciding whether to auto-file an incident.

Detection goal:
{goal}

Observation results:
{tool_results}

Return ONLY a JSON object with exactly these keys:
{{
  "issue_detected": true,
  "title": "short incident title",
  "severity": "critical|high|medium|low",
  "description": "concise evidence-backed explanation",
  "fingerprint": "stable-id-for-this-condition"
}}

If no incident should be created, return:
{{
  "issue_detected": false,
  "title": "",
  "severity": "{severity_default}",
  "description": "brief reason no issue was detected",
  "fingerprint": ""
}}
"""


@dataclasses.dataclass
class DetectorRunResult:
    success: bool
    issue_detected: bool = False
    incident_id: uuid.UUID | None = None
    error: str | None = None
    raw_verdict: dict[str, Any] | None = None


class DetectorBudgetGuard:
    """Simple in-memory rolling-hour guardrails for detector runs."""

    def __init__(self, *, max_runs_per_hour: int = 12, global_budget: int = 500) -> None:
        self.max_runs_per_hour = max_runs_per_hour
        self.global_budget = global_budget
        self._rule_runs: dict[uuid.UUID, deque[datetime]] = {}
        self._global_runs: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    async def check_and_record(self, rule_id: uuid.UUID) -> str | None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=1)
        async with self._lock:
            while self._global_runs and self._global_runs[0] < cutoff:
                self._global_runs.popleft()

            rule_runs = self._rule_runs.setdefault(rule_id, deque())
            while rule_runs and rule_runs[0] < cutoff:
                rule_runs.popleft()

            if self.max_runs_per_hour > 0 and len(rule_runs) >= self.max_runs_per_hour:
                return (
                    f"Detector max-runs-per-hour exceeded for rule {rule_id} "
                    f"({self.max_runs_per_hour}/hour)"
                )
            if self.global_budget > 0 and len(self._global_runs) >= self.global_budget:
                return f"Global detector budget exhausted ({self.global_budget}/hour)"

            rule_runs.append(now)
            self._global_runs.append(now)
            return None


def _is_read_only_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return lowered.startswith(READ_ONLY_PREFIXES)


def _detector_skill_from_tools(tools: list[Tool]) -> SkillDefinition:
    ops = [
        OperationClassification(
            tool=tool.name,
            classification="safe" if _is_read_only_tool(tool.name) else "destructive",
        )
        for tool in tools
    ]
    ops.append(OperationClassification(tool="*", classification="destructive"))
    return SkillDefinition(version="1", environment="detector", operations=ops)


def _tool_lines(tools: list[Tool]) -> str:
    lines: list[str] = []
    for tool in tools:
        description = getattr(tool, "description", None) or ""
        input_schema = getattr(tool, "inputSchema", None) or {}
        schema_text = ""
        if input_schema:
            schema_text = f" schema={json.dumps(input_schema, separators=(',', ':'))[:240]}"
        lines.append(f"- {tool.name}: {description.strip() or 'no description'}{schema_text}")
    return "\n".join(lines) if lines else "- none"


def _extract_json(text: str, *, expect_array: bool) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    opener, closer = ("[", "]") if expect_array else ("{", "}")
    start = text.find(opener)
    end = text.rfind(closer)
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return JSON")
    snippet = text[start : end + 1]
    return json.loads(snippet)


async def _complete(prompt: str, provider) -> str:
    return await asyncio.to_thread(provider.complete, prompt)


def _normalize_severity(raw: str | None, fallback: str) -> str:
    value = (raw or fallback or "medium").strip().lower()
    if value not in {"critical", "high", "medium", "low"}:
        return fallback
    return value


def _normalize_plan(raw: Any, skill_def: SkillDefinition) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in raw[:3]:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        if not tool_name:
            continue
        if skill_def.classify(tool_name) != "safe":
            continue
        params = item.get("tool_parameters") or {}
        if not isinstance(params, dict):
            params = {}
        actions.append(
            {
                "tool_name": tool_name,
                "tool_parameters": params,
                "justification": str(item.get("justification") or ""),
            }
        )
    return actions


def _content_to_text(content_items: list[Any] | None) -> str:
    if not content_items:
        return ""
    parts: list[str] = []
    for item in content_items:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
            continue
        item_type = getattr(item, "type", None)
        if item_type:
            parts.append(f"[{item_type}]")
    return "\n".join(parts).strip()


def _fallback_fingerprint(title: str, description: str) -> str:
    raw = f"{title}\n{description}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:24]


def _resolve_model_kwargs(config: AppConfig, model_cfg) -> dict[str, Any]:
    if model_cfg is not None:
        return {
            "provider": model_cfg.provider,
            "model_id": model_cfg.model_id,
            "api_key_env_var": model_cfg.api_key_env_var,
            "base_url": model_cfg.base_url,
            "api_version": model_cfg.api_version,
            "max_tokens": model_cfg.max_tokens,
        }

    return {
        "provider": config.providers.active_provider,
        "model_id": config.providers.active_model_id,
        "base_url": config.providers.ollama_base_url
        if config.providers.active_provider == "ollama"
        else config.providers.azure_openai_endpoint,
        "api_version": config.providers.azure_openai_api_version,
    }


async def _upsert_detector_incident(
    db: AsyncSession,
    *,
    rule: DetectorRule,
    title: str,
    description: str,
    severity: str,
    fingerprint: str,
) -> Incident:
    external_source = f"detector:{rule.id}"
    existing = await IncidentRepo.get_by_external_fingerprint(
        db,
        org_id=rule.org_id,
        external_source=external_source,
        external_id=fingerprint,
    )
    if existing is not None:
        return existing

    incident = Incident(
        org_id=rule.org_id,
        title=title,
        description=description,
        severity=severity,
        status="open",
        external_source=external_source,
        external_id=fingerprint,
    )
    db.add(incident)
    await db.flush()
    return incident


async def run_detector_rule(
    db: AsyncSession,
    *,
    rule: DetectorRule,
    pool: MCPServerPool,
    config: AppConfig,
    budget_guard: DetectorBudgetGuard | None = None,
) -> DetectorRunResult:
    started = time.monotonic()
    now = datetime.now(timezone.utc)

    if budget_guard is not None:
        budget_error = await budget_guard.check_and_record(rule.id)
        if budget_error:
            await DetectorRuleRepo.mark_run(db, rule.org_id, rule.id, last_ran_at=now)
            await DetectorHistoryRepo.create(
                db,
                rule.org_id,
                rule_id=rule.id,
                duration_ms=0,
                issue_detected=False,
                error=budget_error,
            )
            return DetectorRunResult(success=False, error=budget_error)

    model_cfg = None
    if rule.model_config_id is not None:
        model_cfg = await ModelConfigRepo.get_by_id(db, rule.org_id, rule.model_config_id)
        if model_cfg is None:
            error = f"Model config not found: {rule.model_config_id}"
            await DetectorRuleRepo.mark_run(db, rule.org_id, rule.id, last_ran_at=now)
            await DetectorHistoryRepo.create(
                db,
                rule.org_id,
                rule_id=rule.id,
                duration_ms=0,
                issue_detected=False,
                error=error,
            )
            return DetectorRunResult(success=False, error=error)
    else:
        model_cfg = await ModelConfigRepo.get_default(db, rule.org_id)

    try:
        provider = create_provider(**_resolve_model_kwargs(config, model_cfg))

        async with pool.connect(
            rule.org_id,
            await _resolve_server_name(pool, rule.org_id, rule.mcp_server_id, db),
        ) as session:
            tools = await list_tools(session)
            skill_def = _detector_skill_from_tools(tools)
            safe_tools = [tool for tool in tools if skill_def.classify(tool.name) == "safe"]

            if not safe_tools:
                raise RuntimeError("No safe read-only MCP tools available for detector")

            raw_plan_text = await _complete(
                PLAN_PROMPT.format(
                    server_name=await _resolve_server_name(pool, rule.org_id, rule.mcp_server_id, db),
                    goal=rule.prompt_template,
                    tool_lines=_tool_lines(safe_tools),
                ),
                provider,
            )
            raw_plan = _extract_json(raw_plan_text, expect_array=True)
            actions = _normalize_plan(raw_plan, skill_def)

            tool_results: list[dict[str, Any]] = []
            for action in actions:
                result = await call_tool(
                    session,
                    action["tool_name"],
                    action["tool_parameters"],
                )
                tool_results.append(
                    {
                        "tool_name": action["tool_name"],
                        "tool_parameters": action["tool_parameters"],
                        "is_error": getattr(result, "isError", False),
                        "content_text": _content_to_text(getattr(result, "content", None)),
                    }
                )

        raw_verdict_text = await _complete(
            VERDICT_PROMPT.format(
                goal=rule.prompt_template,
                tool_results=json.dumps(tool_results, indent=2),
                severity_default=rule.severity_default,
            ),
            provider,
        )
        verdict = _extract_json(raw_verdict_text, expect_array=False)
        if not isinstance(verdict, dict):
            raise ValueError("Detector verdict must be a JSON object")

        issue_detected = bool(verdict.get("issue_detected"))
        fingerprint = str(verdict.get("fingerprint") or "").strip()
        incident_id: uuid.UUID | None = None

        if issue_detected:
            title = str(verdict.get("title") or "Detected incident").strip() or "Detected incident"
            description = str(verdict.get("description") or rule.prompt_template).strip() or rule.prompt_template
            severity = _normalize_severity(verdict.get("severity"), rule.severity_default)
            if not fingerprint:
                fingerprint = _fallback_fingerprint(title, description)
            incident = await _upsert_detector_incident(
                db,
                rule=rule,
                title=title,
                description=description,
                severity=severity,
                fingerprint=fingerprint,
            )
            incident_id = incident.id

        await DetectorRuleRepo.mark_run(
            db,
            rule.org_id,
            rule.id,
            last_ran_at=now,
            last_fingerprint=fingerprint or None,
        )
        await DetectorHistoryRepo.create(
            db,
            rule.org_id,
            rule_id=rule.id,
            duration_ms=int((time.monotonic() - started) * 1000),
            issue_detected=issue_detected,
            incident_id=incident_id,
            raw_verdict=verdict,
        )
        return DetectorRunResult(
            success=True,
            issue_detected=issue_detected,
            incident_id=incident_id,
            raw_verdict=verdict,
        )
    except Exception as exc:  # noqa: BLE001
        await DetectorRuleRepo.mark_run(db, rule.org_id, rule.id, last_ran_at=now)
        await DetectorHistoryRepo.create(
            db,
            rule.org_id,
            rule_id=rule.id,
            duration_ms=int((time.monotonic() - started) * 1000),
            issue_detected=False,
            error=str(exc),
        )
        return DetectorRunResult(success=False, error=str(exc))


async def _resolve_server_name(
    pool: MCPServerPool,
    org_id: uuid.UUID,
    mcp_server_id: uuid.UUID,
    db: AsyncSession,
) -> str:
    from backend.db.repos import MCPServerRepo  # local import avoids cycle

    server = await MCPServerRepo.get_by_id(db, org_id, mcp_server_id)
    if server is None:
        raise RuntimeError(f"MCP server not found: {mcp_server_id}")
    resolved = await pool.get_server(org_id, server.name)
    if resolved is None:
        raise RuntimeError(f"MCP server is not currently available: {server.name}")
    return server.name
