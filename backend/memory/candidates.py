"""Extract memory candidates from a postmortem (v1.2 postmortem → memory handoff).

A postmortem's **Memory candidates** section is a bullet list of short, durable
lessons the author wants to feed into AI incident memory. This module pulls those
bullets out of the markdown so the postmortem route can turn each into a
``pending`` incident memory (which then flows through the Phase 1 review queue).

Pure + deterministic — no DB, no LLM.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^#{1,6}\s*memory\s+candidates\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_COMMENT_RE = re.compile(r"^<!--.*-->$")
# Italic-only placeholder, e.g. "_..._" or "_Add a memory here_".
_ITALIC_PLACEHOLDER_RE = re.compile(r"^_.*_$")

_TITLE_MAX = 200


def _is_placeholder(text: str) -> bool:
    """True when a bullet is template scaffolding rather than a real lesson."""
    if not text:
        return True
    if _COMMENT_RE.match(text):
        return True
    if _ITALIC_PLACEHOLDER_RE.match(text):
        return True
    # A lone ellipsis / dash placeholder.
    if text in {"...", "…", "-", "—"}:
        return True
    return False


def extract_memory_candidates(postmortem_md: str | None) -> list[str]:
    """Return the cleaned bullet lessons under the *Memory candidates* heading.

    Skips template placeholders (HTML comments, italic prompts, empty bullets)
    and de-duplicates case-insensitively while preserving order. Returns an
    empty list when the section is missing or holds only scaffolding.
    """
    if not postmortem_md:
        return []

    lines = postmortem_md.splitlines()
    in_section = False
    out: list[str] = []
    seen: set[str] = set()

    for line in lines:
        if not in_section:
            if _HEADING_RE.match(line.strip()):
                in_section = True
            continue
        # A new heading of any level ends the Memory candidates section.
        if _ANY_HEADING_RE.match(line):
            break
        match = _BULLET_RE.match(line)
        if not match:
            continue
        text = match.group(1).strip()
        # Strip a trailing inline comment, then re-check.
        text = re.sub(r"\s*<!--.*-->\s*$", "", text).strip()
        if _is_placeholder(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)

    return out


def candidate_title(candidate: str) -> str:
    """Derive a concise memory title from a candidate bullet."""
    text = candidate.strip()
    if len(text) <= _TITLE_MAX:
        return text
    return text[: _TITLE_MAX - 1].rstrip() + "…"
