"""Default analyzer shipped with the Auditor (Sprint 32 + simplification pass).

A single ``EnvironmentScanAnalyzer`` is the only analyzer registered out of
the box. It is intentionally platform-agnostic — it ships zero knowledge of
Kubernetes, ECS, Cloud Run, systemd, or any specific runtime. The operator
brings the MCP server; the analyzer asks the model to look at what that
server exposes and summarize anything concerning into structured findings.

Per D-001 (MCP-first) and D-023 (deployment-platform agnosticism), any
platform-specific behavior is encoded by the operator either:

* in the MCP server itself (which tools it exposes), or
* in the SKILL.md attached to that server (which operations are safe to
  observe, and — optionally — what *focus areas* the LLM should weight
  the scan toward).

Operator-authored example adapters for kube-score / istioctl-analyze live
in :mod:`backend.auditor.example_analyzers` for reference only — they are
not registered by default.
"""

from __future__ import annotations


from backend.auditor._helpers import (
    execute_call,
    make_call,
    parse_json,
    resolve_provider_kwargs,
)
from backend.auditor.base import Analyzer, AnalyzerContext, FindingDraft


def _format_focus_areas(focus_areas: list[str] | None) -> str:
    if not focus_areas:
        return (
            "No specific focus areas configured. Summarize any anomalies, "
            "misconfigurations, or signals of pending failure that you can "
            "infer from the observations."
        )
    bullets = "\n".join(f"- {item}" for item in focus_areas if item)
    return (
        "Weight the scan toward the following operator-configured focus "
        "areas (defined in the MCP server's SKILL.md):\n" + bullets
    )


class EnvironmentScanAnalyzer(Analyzer):
    """LLM-driven environment scan. The only default analyzer.

    Flow:
    1. List the MCP server's read-only tools (skill-classified ``safe``).
    2. Ask the LLM to pick at most three observations to run, weighted by
       the operator's ``focus_areas`` from SKILL.md (when present).
    3. Execute the chosen observations.
    4. Ask the LLM to summarize anomalies into a JSON list of findings.

    The analyzer never assumes any specific platform. Whether the server
    serves Kubernetes, ECS, Azure Container Apps, GCP Cloud Run, OCI
    Container Instances, Nomad, systemd, or anything else, the same code
    path runs.
    """

    key = "environment-scan"
    label = "Environment Scan"
    description = (
        "LLM-driven scan against the chosen MCP server. Platform-agnostic. "
        "Honors the SKILL.md focus_areas section when present."
    )

    PROMPT = (
        "You are an SRE auditor inspecting a deployed environment. You only "
        "see what the configured MCP server exposes — the framework knows "
        "nothing about the underlying platform (Kubernetes / ECS / Azure / "
        "GCP / OCI / monolithic VMs / etc.).\n\n"
        "{focus_block}\n\n"
        "Observation payload (from a read-only MCP tool call):\n"
        "{payload}\n\n"
        "Return ONLY a JSON array of findings. Each finding must be an "
        "object with keys: severity (critical|high|medium|low|info), "
        "category, resource, message, suggested_fix (optional). Return [] "
        "if nothing actionable."
    )

    async def run(self, ctx: AnalyzerContext) -> list[FindingDraft]:
        from backend.db.repos import (
            ModelConfigRepo,
        )
        from backend.llm.factory import create_provider

        server_name = ctx.params.get("mcp_server_name")
        focus_areas = await _load_focus_areas(ctx, server_name=server_name)
        # Param-supplied focus_areas override SKILL-derived ones so operators
        # can narrow a single run from the UI without editing the skill file.
        if ctx.params.get("focus_areas"):
            focus_areas = list(ctx.params["focus_areas"])

        observe_tool = ctx.params.get("tool_name") or "describe"
        observe_params = ctx.params.get("tool_params") or {}

        call = make_call(ctx, tool_name=observe_tool, params=observe_params)
        observations = await execute_call(ctx, call)

        model_cfg = await ModelConfigRepo.get_default(ctx.db, ctx.org_id)
        provider_kwargs = resolve_provider_kwargs(ctx.config, model_cfg)
        provider = create_provider(**provider_kwargs)
        prompt = self.PROMPT.format(
            focus_block=_format_focus_areas(focus_areas),
            payload=observations[:6000],
        )
        response = await provider.complete(prompt)
        return self.parse(response)

    def parse(self, response: str) -> list[FindingDraft]:
        parsed = parse_json(response)
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


async def _load_focus_areas(
    ctx: AnalyzerContext, *, server_name: str | None
) -> list[str]:
    """Look up the skill bound to ``server_name`` and return its focus areas.

    Returns an empty list when no skill exists, no MCP server matches, or
    the skill has no ``focus_areas`` configured.
    """

    if not server_name:
        return []
    from backend.db.repos import MCPServerRepo, SkillRepo
    from backend.skills.parser import loads as parse_skill

    server = await MCPServerRepo.get_by_name(ctx.db, ctx.org_id, server_name)
    if server is None:
        return []
    skill = await SkillRepo.get_for_mcp_server(ctx.db, ctx.org_id, server.id)
    if skill is None or not skill.content_md:
        return []
    try:
        definition = parse_skill(skill.content_md, fmt="md")
    except Exception:  # noqa: BLE001 — parsing must never break the scan
        return []
    return list(getattr(definition, "focus_areas", []) or [])
