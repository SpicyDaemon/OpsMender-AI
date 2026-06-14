"""Heuristic MCP tool classification suggestions (MCP Skill Studio generator).

When an operator discovers an MCP server's tools in the Skill Studio, OpsMender
suggests a starting classification for each tool from its **name** alone. These
are *suggestions only* — the operator reviews and overrides them, and the
backend tier gate (``backend/tiers/enforcement.py``) is always the execution
authority. The suggester is deliberately conservative and fail-safe:

  - Generic command-execution tools (``shell``, ``kubectl``, ``run_command`` …)
    are flagged and suggested **deny** — their name does not bound what they do.
  - Clear read verbs (``get``/``list``/``describe`` …) → ``safe``.
  - Clear destructive verbs (``delete``/``destroy``/``drop`` …) → ``destructive``.
  - Reversible-write verbs (``restart``/``scale``/``update`` …) → ``caution``.
  - Anything unrecognized → ``caution`` + ``needs_review`` (never silently safe).

This module is pure and deterministic — no network, no LLM.
"""

from __future__ import annotations

import dataclasses

from backend.tiers.generic_tools import is_generic_execution_tool

# First-token (verb) vocabularies. Matched case-insensitively against the
# leading token of the tool name (split on non-alphanumeric separators).
_SAFE_VERBS: frozenset[str] = frozenset(
    {
        "get", "list", "describe", "read", "show", "fetch", "search", "watch",
        "view", "lookup", "find", "status", "check", "inspect", "scan", "tail",
        "count", "summarize", "report", "ping", "head", "stat", "diff", "explain",
    }
)
_DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {
        "delete", "destroy", "drop", "remove", "terminate", "purge", "wipe",
        "kill", "truncate", "revoke", "uninstall", "decommission", "teardown",
        "force", "erase", "expire", "evict",
    }
)
_CAUTION_VERBS: frozenset[str] = frozenset(
    {
        "restart", "scale", "update", "create", "apply", "patch", "set",
        "rollback", "rollout", "cordon", "drain", "deploy", "start", "stop",
        "enable", "disable", "add", "put", "modify", "edit", "write", "rotate",
        "resize", "reboot", "failover", "promote", "attach", "detach",
        "register", "deregister", "schedule", "cancel", "pause", "resume",
        "scale_up", "scale_down", "label", "annotate", "tag", "move", "copy",
        "import", "export", "sync", "trigger", "invoke", "send", "post",
    }
)


@dataclasses.dataclass(frozen=True)
class ClassificationSuggestion:
    """A suggested classification for one discovered tool."""

    classification: str  # "safe" | "caution" | "destructive"
    generic: bool  # arbitrary-command tool (guardrailed)
    deny: bool  # suggest an explicit deny-list entry
    needs_review: bool  # name was unrecognized — operator should confirm
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "generic": self.generic,
            "deny": self.deny,
            "needs_review": self.needs_review,
            "rationale": self.rationale,
        }


def _leading_verb(tool_name: str) -> str:
    token = ""
    for ch in tool_name.strip().lower():
        if ch.isalnum():
            token += ch
        else:
            break
    return token


def suggest_classification(tool_name: str) -> ClassificationSuggestion:
    """Suggest a classification for *tool_name* from its name alone.

    Conservative and fail-safe: generic command tools are suggested deny, and an
    unrecognized verb defaults to ``caution`` (never silently ``safe``).
    """
    name = (tool_name or "").strip()
    if not name:
        return ClassificationSuggestion(
            classification="caution",
            generic=False,
            deny=False,
            needs_review=True,
            rationale="Empty tool name — review before use.",
        )

    if is_generic_execution_tool(name):
        return ClassificationSuggestion(
            classification="destructive",
            generic=True,
            deny=True,
            needs_review=True,
            rationale=(
                "Runs arbitrary commands — its name does not bound what it can "
                "do. Suggested deny; opt out only for a narrowly-scoped wrapper."
            ),
        )

    verb = _leading_verb(name)
    if verb in _DESTRUCTIVE_VERBS:
        return ClassificationSuggestion(
            classification="destructive",
            generic=False,
            deny=False,
            needs_review=False,
            rationale=f"'{verb}' suggests a high-risk / irreversible action.",
        )
    if verb in _SAFE_VERBS:
        return ClassificationSuggestion(
            classification="safe",
            generic=False,
            deny=False,
            needs_review=False,
            rationale=f"'{verb}' suggests a read-only / low-risk action.",
        )
    if verb in _CAUTION_VERBS:
        return ClassificationSuggestion(
            classification="caution",
            generic=False,
            deny=False,
            needs_review=False,
            rationale=f"'{verb}' suggests a reversible write — review reversibility.",
        )

    return ClassificationSuggestion(
        classification="caution",
        generic=False,
        deny=False,
        needs_review=True,
        rationale="Unrecognized action — defaulting to caution; classify it.",
    )
