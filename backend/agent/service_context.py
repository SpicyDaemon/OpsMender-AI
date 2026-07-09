"""Service context for the AI agent prompt (v1.2 Phase 5).

When an incident belongs to a Service, the agent benefits from knowing what that
service is, how important it is, and which MCP servers are allowed for it.
This module builds a small, plain-text block that the session runner appends to
the incident description so the very first ``observe`` pass is grounded in the
service it is actually working on.

Pure + deterministic — no DB. The runner resolves the service and passes its
fields here.
"""

from __future__ import annotations


def format_service_context(
    *,
    name: str | None,
    priority: str | None = None,
    description: str | None = None,
    allowed_mcp_names: list[str] | None = None,
) -> str:
    """Return a ``## Service context`` block, or ``""`` when there is no service.

    Only non-empty fields are included so the block stays terse.
    """
    if not name or not name.strip():
        return ""

    lines = ["## Service context", f"- Service: {name.strip()}"]
    if priority and priority.strip():
        lines.append(f"- Priority: {priority.strip()}")
    if description and description.strip():
        lines.append(f"- Description: {description.strip()}")
    names = [n.strip() for n in (allowed_mcp_names or []) if n and n.strip()]
    if names:
        lines.append("- Allowed MCP servers: " + ", ".join(names))
    return "\n".join(lines)
