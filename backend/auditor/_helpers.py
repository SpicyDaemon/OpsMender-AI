"""Shared helpers for auditor analyzers.

Pulled out of ``analyzers.py`` so the default LLM-driven analyzer and the
optional example adapters (``example_analyzers.py``) can share JSON parsing,
MCP call plumbing, and LLM provider resolution without circular imports.
"""

from __future__ import annotations

import json
from typing import Any

from backend.auditor.base import AnalyzerContext, AnalyzerError
from backend.mcp.client import call_tool


def content_to_text(content_items: list[Any] | None) -> str:
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


def parse_json(text: str) -> Any:
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


class CallSpec:
    """Internal handle for one MCP tool call."""

    def __init__(
        self, *, server_name: str, tool_name: str, params: dict[str, Any]
    ) -> None:
        self.server_name = server_name
        self.tool_name = tool_name
        self.params = params


def make_call(
    ctx: AnalyzerContext, *, tool_name: str, params: dict[str, Any]
) -> CallSpec:
    """Build a :class:`CallSpec` against the MCP server in ``ctx.params``."""

    server_name = ctx.params.get("mcp_server_name")
    if not server_name:
        raise AnalyzerError("Analyzer requires `mcp_server_name` in params")
    return CallSpec(server_name=server_name, tool_name=tool_name, params=params)


async def execute_call(ctx: AnalyzerContext, call: CallSpec) -> str:
    async with ctx.pool.connect(ctx.org_id, call.server_name) as session:
        result = await call_tool(session, call.tool_name, call.params)
        if getattr(result, "isError", False):
            raise AnalyzerError(
                f"MCP tool {call.tool_name} on {call.server_name} returned an error"
            )
        return content_to_text(getattr(result, "content", None))


def resolve_provider_kwargs(config, model_cfg) -> dict[str, Any]:
    """Build create_provider/create_llm kwargs from a DB model row OR env.

    Per-provider env-only path (when model_cfg is None):
      - ollama             → OLLAMA_BASE_URL
      - azure_openai       → AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_VERSION
      - openai_compatible  → OPSMENDER_OPENAI_COMPATIBLE_BASE_URL
                             + optional OPSMENDER_OPENAI_COMPATIBLE_API_KEY_ENV_VAR
      - others (anthropic / openai / bedrock / vertex_ai) → no base_url
        needed; their credentials/region/project resolve via their own
        SDK conventions (env API keys, AWS chain, ADC).
    """
    if model_cfg is not None:
        return {
            "provider": model_cfg.provider,
            "model_id": model_cfg.model_id,
            "api_key_env_var": model_cfg.api_key_env_var,
            "base_url": model_cfg.base_url,
            "api_version": model_cfg.api_version,
            "provider_meta": model_cfg.provider_meta,
            "max_tokens": model_cfg.max_tokens,
        }
    providers = config.providers
    active = providers.active_provider
    base_url: str | None
    api_key_env_var: str | None = None
    api_version: str | None = None
    if active == "ollama":
        base_url = providers.ollama_base_url
    elif active == "azure_openai":
        base_url = providers.azure_openai_endpoint
        api_version = providers.azure_openai_api_version
    elif active == "openai_compatible":
        base_url = providers.openai_compatible_base_url
        api_key_env_var = providers.openai_compatible_api_key_env_var
    else:
        base_url = None
    return {
        "provider": active,
        "model_id": providers.active_model_id,
        "base_url": base_url,
        "api_key_env_var": api_key_env_var,
        "api_version": api_version,
    }
