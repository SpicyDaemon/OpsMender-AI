"""Built-in analyzers for the Auditor v1 (Sprint 32).

Three adapters ship with the v1 sprint:

* ``kube-score`` — wraps ``kube-score score -o json`` exposed as an MCP tool.
* ``istioctl-analyze`` — wraps ``istioctl analyze -n <ns> -o json``.
* ``generic-llm-analyzer`` — discovers anomalies by asking an LLM to summarize
  the output of read-only tools on any MCP server (the closest analog to the
  legacy detector loop).

Each adapter calls a configured MCP tool through the existing pool / skill /
tier enforcement layer. The output is parsed into :class:`FindingDraft`
instances. Parsing is intentionally defensive — analyzers must never raise
on a malformed payload; they emit a single "info" finding with the raw text
instead so the operator can still triage what came back.
"""

from __future__ import annotations

import json
from typing import Any

from backend.auditor.base import (
    Analyzer,
    AnalyzerContext,
    AnalyzerError,
    FindingDraft,
)
from backend.mcp.client import call_tool


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


def _parse_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    # Strip optional ```json fences a tool may emit.
    if text.startswith("```"):
        fence_end = text.find("\n")
        if fence_end > 0:
            text = text[fence_end + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _mcp_call(ctx: AnalyzerContext, *, tool_name: str, params: dict[str, Any]) -> Any:
    """Call an MCP tool inside the analyzer's configured server.

    Returns the parsed JSON payload (preferred) or raw text if the tool did
    not emit JSON. Raises :class:`AnalyzerError` if the MCP server is missing
    or the tool errored.
    """

    server_name = ctx.params.get("mcp_server_name")
    if not server_name:
        raise AnalyzerError(
            "Analyzer requires `mcp_server_name` in params"
        )
    return _CallSpec(server_name=server_name, tool_name=tool_name, params=params)


# Container used by analyzers to describe a single MCP call; the runner
# executes these so analyzers stay easy to unit-test (no live MCP needed).
class _CallSpec:
    def __init__(
        self, *, server_name: str, tool_name: str, params: dict[str, Any]
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.params = params


async def _execute_call(ctx: AnalyzerContext, call: _CallSpec) -> str:
    async with ctx.pool.connect(ctx.org_id, call.server_name) as session:
        result = await call_tool(session, call.tool_name, call.params)
        if getattr(result, "isError", False):
            raise AnalyzerError(
                f"MCP tool {call.tool_name} on {call.server_name} returned an error"
            )
        return _content_to_text(getattr(result, "content", None))


# ---------------------------------------------------------------------------
# kube-score
# ---------------------------------------------------------------------------


class KubeScoreAnalyzer(Analyzer):
    """Wrap ``kube-score score -o json`` exposed via an MCP server.

    Expected MCP tool conventions: a tool that scores a manifest set and
    returns a JSON array where each element has ``object_meta`` and
    ``checks``. The parser is intentionally tolerant — any shape that maps
    to a list of dicts with ``check_name`` + ``grade`` works.
    """

    key = "kube-score"
    label = "kube-score"
    description = (
        "Scores Kubernetes manifests via kube-score and surfaces grade < 10 checks."
    )

    GRADE_TO_SEVERITY = {
        1: "critical",
        2: "critical",
        3: "high",
        4: "high",
        5: "medium",
        6: "medium",
        7: "low",
        8: "low",
        9: "low",
        10: "info",
    }

    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        tool_name = ctx.params.get("tool_name", "kube_score")
        namespace = ctx.params.get("namespace", "default")
        call = _mcp_call(
            ctx, tool_name=tool_name, params={"namespace": namespace}
        )
        raw_text = await _execute_call(ctx, call)
        return self.parse(raw_text)

    def parse(self, raw_text: str) -> list[FindingDraft]:
        parsed = _parse_json(raw_text)
        if not isinstance(parsed, list):
            if raw_text.strip():
                return [
                    FindingDraft(
                        analyzer=self.key,
                        severity="info",
                        message=f"kube-score returned non-JSON output:\n{raw_text[:1500]}",
                    )
                ]
            return []
        findings: list[FindingDraft] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            meta = item.get("object_meta") or {}
            kind = (
                item.get("type_meta", {}).get("kind")
                or item.get("kind")
                or "Object"
            )
            resource = f"{kind}/{meta.get('name', '?')}"
            ns = meta.get("namespace")
            if ns:
                resource = f"{resource} in ns {ns}"
            for check in item.get("checks") or []:
                if not isinstance(check, dict):
                    continue
                grade = int(check.get("grade") or 10)
                if grade >= 10:
                    continue
                severity = self.GRADE_TO_SEVERITY.get(grade, "info")
                comments = check.get("comments") or []
                comment_text = (
                    "; ".join(
                        f"{c.get('summary', '')}: {c.get('description', '')}".strip(": ")
                        for c in comments
                        if isinstance(c, dict)
                    )
                    if isinstance(comments, list)
                    else ""
                )
                findings.append(
                    FindingDraft(
                        analyzer=self.key,
                        severity=severity,
                        category=str(check.get("check_name") or "kube-score"),
                        resource=resource,
                        message=comment_text
                        or str(check.get("check_name") or "kube-score check failed"),
                    )
                )
        return findings


# ---------------------------------------------------------------------------
# istioctl analyze
# ---------------------------------------------------------------------------


class IstioctlAnalyzeAnalyzer(Analyzer):
    """Wrap ``istioctl analyze -n <ns> -o json``."""

    key = "istioctl-analyze"
    label = "istioctl analyze"
    description = (
        "Runs istioctl analyze against a namespace and reports each diagnostic."
    )

    LEVEL_TO_SEVERITY = {
        "Error": "high",
        "Warning": "medium",
        "Info": "info",
    }

    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        tool_name = ctx.params.get("tool_name", "istioctl_analyze")
        namespace = ctx.params.get("namespace", "default")
        call = _mcp_call(
            ctx, tool_name=tool_name, params={"namespace": namespace}
        )
        raw_text = await _execute_call(ctx, call)
        return self.parse(raw_text, namespace=namespace)

    def parse(self, raw_text: str, *, namespace: str = "default") -> list[FindingDraft]:
        parsed = _parse_json(raw_text)
        # istioctl analyze JSON shape: a list of {"code", "level", "message",
        # "origin", "documentation_url"} entries, OR an object with "messages".
        items: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            items = [p for p in parsed if isinstance(p, dict)]
        elif isinstance(parsed, dict):
            msgs = parsed.get("messages")
            if isinstance(msgs, list):
                items = [m for m in msgs if isinstance(m, dict)]
        if not items:
            if raw_text.strip():
                return [
                    FindingDraft(
                        analyzer=self.key,
                        severity="info",
                        message=f"istioctl analyze returned no diagnostics or non-JSON:\n{raw_text[:1500]}",
                    )
                ]
            return []

        findings: list[FindingDraft] = []
        for item in items:
            level = str(item.get("level") or "Info").strip().title()
            severity = self.LEVEL_TO_SEVERITY.get(level, "info")
            code = str(item.get("code") or "")
            message = str(item.get("message") or item.get("description") or "")
            origin = item.get("origin")
            resource = None
            if isinstance(origin, str):
                resource = origin
            elif isinstance(origin, dict):
                resource = origin.get("name") or origin.get("resource")
            findings.append(
                FindingDraft(
                    analyzer=self.key,
                    severity=severity,
                    category=code or "istio-diagnostic",
                    resource=resource or f"namespace/{namespace}",
                    message=message or f"istioctl analyze code {code}",
                    suggested_fix=item.get("documentation_url"),
                )
            )
        return findings


# ---------------------------------------------------------------------------
# Generic LLM analyzer
# ---------------------------------------------------------------------------


class GenericLLMAnalyzer(Analyzer):
    """Run an LLM-driven scan against any read-only MCP server.

    This is the fallback for environments where no static analyzer applies —
    the operator points the analyzer at an MCP server (e.g. plain kubectl)
    and provides a free-form goal prompt. The analyzer asks the model to
    summarize anomalies and return a JSON list of findings.

    Network calls (MCP + LLM) are wrapped so unit tests can override the
    ``invoke_llm`` hook.
    """

    key = "generic-llm-analyzer"
    label = "Generic LLM"
    description = (
        "LLM-driven anomaly summarization against any read-only MCP server."
    )

    PROMPT = (
        "You are an SRE auditor. Inspect the following observation payload "
        "from an MCP server and return ONLY a JSON array of findings. Each "
        "finding must be an object with keys severity "
        "(critical|high|medium|low|info), category, resource, message, "
        "suggested_fix (optional). Return [] if nothing actionable.\n\n"
        "Goal: {goal}\n\nObservations:\n{payload}\n"
    )

    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        from backend.db.repos import ModelConfigRepo
        from backend.llm.factory import create_provider

        goal = (ctx.params.get("goal") or "Summarize any anomalies.").strip()
        observe_tool = ctx.params.get("tool_name") or "describe"
        observe_params = ctx.params.get("tool_params") or {}

        call = _mcp_call(ctx, tool_name=observe_tool, params=observe_params)
        observations = await _execute_call(ctx, call)

        model_cfg = await ModelConfigRepo.get_default(ctx.db, ctx.org_id)
        provider_kwargs = _resolve_provider_kwargs(ctx.config, model_cfg)
        provider = create_provider(**provider_kwargs)
        prompt = self.PROMPT.format(goal=goal, payload=observations[:6000])
        response = await provider.complete(prompt)
        return self.parse(response)

    def parse(self, response: str) -> list[FindingDraft]:
        parsed = _parse_json(response)
        if not isinstance(parsed, list):
            stripped = (response or "").strip()
            if not stripped:
                return []
            return [
                FindingDraft(
                    analyzer=self.key,
                    severity="info",
                    message=f"LLM returned non-JSON output:\n{stripped[:1500]}",
                )
            ]
        findings: list[FindingDraft] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            findings.append(
                FindingDraft(
                    analyzer=self.key,
                    severity=str(item.get("severity") or "info"),
                    category=item.get("category"),
                    resource=item.get("resource"),
                    message=str(item.get("message") or "(no message)"),
                    suggested_fix=item.get("suggested_fix"),
                )
            )
        return findings


def _resolve_provider_kwargs(config, model_cfg) -> dict[str, Any]:
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
