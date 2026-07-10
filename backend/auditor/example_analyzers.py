"""Example operator-authored analyzers — NOT registered by default.

These classes are kept as documentation of how an operator can write a
domain-specific analyzer that wraps a known MCP tool with deterministic
parsing. They exist as reference only.

Per D-001 (MCP-first) and D-023 (deployment-platform agnosticism), the
OpsMender framework ships no platform-specific knowledge. Anything that
mentions a specific runtime (Kubernetes, Istio, ECS, …) lives here as a
worked example, and is registered by the operator at startup if they want
the deterministic behavior.

To register one of these in a deployment::

    from backend.auditor import register_analyzer
    from backend.auditor.example_analyzers import KubeScoreAnalyzer

    register_analyzer(KubeScoreAnalyzer())
"""

from __future__ import annotations

from typing import Any

from backend.auditor._helpers import execute_call, make_call, parse_json
from backend.auditor.base import Analyzer, AnalyzerContext, FindingDraft


# ---------------------------------------------------------------------------
# kube-score (example — Kubernetes-specific)
# ---------------------------------------------------------------------------


class KubeScoreAnalyzer(Analyzer):
    """Example: wrap ``kube-score score -o json`` exposed via an MCP server.

    Expected MCP tool conventions: a tool that scores a manifest set and
    returns a JSON array where each element has ``object_meta`` and
    ``checks``. The parser is intentionally tolerant — any shape that maps
    to a list of dicts with ``check_name`` + ``grade`` works.
    """

    key = "kube-score"
    label = "kube-score"
    description = (
        "Example Kubernetes-specific analyzer. Scores manifests via "
        "kube-score and surfaces grade < 10 checks. Not registered by "
        "default — operator must opt in."
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
        call = make_call(ctx, tool_name=tool_name, params={"namespace": namespace})
        raw_text = await execute_call(ctx, call)
        return self.parse(raw_text)

    def parse(self, raw_text: str) -> list[FindingDraft]:
        parsed = parse_json(raw_text)
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
            kind = item.get("type_meta", {}).get("kind") or item.get("kind") or "Object"
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
                        f"{c.get('summary', '')}: {c.get('description', '')}".strip(
                            ": "
                        )
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
# istioctl analyze (example — Istio-specific)
# ---------------------------------------------------------------------------


class IstioctlAnalyzeAnalyzer(Analyzer):
    """Example: wrap ``istioctl analyze -n <ns> -o json``.

    Not registered by default — operator must opt in.
    """

    key = "istioctl-analyze"
    label = "istioctl analyze"
    description = (
        "Example Istio-specific analyzer. Runs istioctl analyze against a "
        "namespace and reports each diagnostic. Not registered by default — "
        "operator must opt in."
    )

    LEVEL_TO_SEVERITY = {
        "Error": "high",
        "Warning": "medium",
        "Info": "info",
    }

    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        tool_name = ctx.params.get("tool_name", "istioctl_analyze")
        namespace = ctx.params.get("namespace", "default")
        call = make_call(ctx, tool_name=tool_name, params={"namespace": namespace})
        raw_text = await execute_call(ctx, call)
        return self.parse(raw_text, namespace=namespace)

    def parse(self, raw_text: str, *, namespace: str = "default") -> list[FindingDraft]:
        parsed = parse_json(raw_text)
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
