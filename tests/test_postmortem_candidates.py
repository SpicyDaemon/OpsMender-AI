"""Unit tests for the postmortem → memory-candidate extractor (v1.2 Phase 2)."""

from __future__ import annotations

from backend.api.schemas import DEFAULT_POSTMORTEM_TEMPLATE
from backend.memory.candidates import (
    candidate_title,
    extract_memory_candidates,
)


def test_empty_or_missing_section_returns_empty():
    assert extract_memory_candidates(None) == []
    assert extract_memory_candidates("") == []
    assert extract_memory_candidates("## Summary\nstuff") == []


def test_default_template_yields_no_candidates():
    # The template only has a hint line, no real bullets.
    assert extract_memory_candidates(DEFAULT_POSTMORTEM_TEMPLATE) == []


def test_extracts_real_bullets_and_skips_scaffolding():
    md = (
        "## Lessons learned\n- not this section\n\n"
        "## Memory candidates\n"
        "<!-- one bullet per memory -->\n"
        "- Restart the worker pool when queue depth > 10k.\n"
        "- _Add a memory here_\n"
        "-\n"
        "* Check JWT clock skew before blaming the gateway.\n"
    )
    assert extract_memory_candidates(md) == [
        "Restart the worker pool when queue depth > 10k.",
        "Check JWT clock skew before blaming the gateway.",
    ]


def test_dedups_case_insensitively_preserving_order():
    md = (
        "## Memory candidates\n"
        "- Cap connections at 100 per pod\n"
        "- cap connections at 100 per pod\n"
        "- Drain before deploy\n"
    )
    assert extract_memory_candidates(md) == [
        "Cap connections at 100 per pod",
        "Drain before deploy",
    ]


def test_heading_is_case_insensitive_and_stops_at_next_heading():
    md = "# Postmortem\n## Memory Candidates\n- Keep this\n## Other\n- Not this\n"
    assert extract_memory_candidates(md) == ["Keep this"]


def test_strips_trailing_inline_comment():
    md = "## Memory candidates\n- Real lesson <!-- note -->\n"
    assert extract_memory_candidates(md) == ["Real lesson"]


def test_candidate_title_truncates_long_text():
    long = "x" * 250
    title = candidate_title(long)
    assert len(title) <= 200
    assert title.endswith("…")
    assert candidate_title("short") == "short"
