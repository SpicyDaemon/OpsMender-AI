"""Regression tests for the alert-title normalizer's regexes.

Alert titles arrive in monitoring webhook payloads, so they are untrusted
input. ``_TRAILING_HOST_RE`` previously had an ambiguity between
``[a-z0-9-]*`` and a following ``\\d+`` (both consume digits), which made the
engine try every split and backtrack exponentially: a 98-character title took
1.7 seconds, and each extra ``00.0`` group quadrupled that.
"""

from __future__ import annotations

import re
import time

import pytest

from backend.ingest.noise import _TRAILING_HOST_RE, normalize_title_tokens

# The ambiguous form that used to ship, kept here so the equivalence test
# proves the replacement did not change which titles get normalized.
_LEGACY_TRAILING_HOST_RE = re.compile(
    r"(?:^|\s)[a-z0-9][a-z0-9-]*\d+(?:\.[a-z0-9][a-z0-9-]*\d+)*\s*$",
    re.IGNORECASE,
)

_EQUIVALENCE_CASES = [
    "",
    "host",
    "web-01",
    "api-02.prod-03",
    "a1",
    "a12",
    "1",
    "x-9",
    "a.b1",
    " srv001 ",
    "a1.b2.c3",
    "--1",
    "a-1.",
    "0x0",
    "pod crashloop on node-17",
    "cpu high web-01.eu-west-1",
    "disk 90% full on db-3",
    "no trailing host here",
    "trailing digits 12345",
    "\t00.0",
]


@pytest.mark.parametrize("title", _EQUIVALENCE_CASES)
def test_trailing_host_matches_the_legacy_pattern(title: str) -> None:
    """The de-ambiguated pattern must accept exactly the same titles."""
    assert _TRAILING_HOST_RE.sub(" ", title) == _LEGACY_TRAILING_HOST_RE.sub(" ", title)


def test_trailing_host_is_linear_on_adversarial_input() -> None:
    """A crafted title must not stall the ingest path.

    The sentinel ``!`` defeats the ``$`` anchor, which is what forced the old
    pattern to explore every backtracking path. 24 repetitions took ~1.7s
    before; the bound here is deliberately loose so the test is not flaky on a
    slow runner, while still failing loudly if the ambiguity comes back.
    """
    payload = "\t" + "00.0" * 24 + "!"
    start = time.perf_counter()
    _TRAILING_HOST_RE.search(payload)
    assert time.perf_counter() - start < 0.5


def test_normalize_title_tokens_still_strips_a_trailing_host() -> None:
    """The behaviour the regex exists for is unchanged."""
    assert "web" not in normalize_title_tokens("cpu saturation on web-01")
    assert normalize_title_tokens("cpu saturation on web-01") == (
        "cpu",
        "saturation",
        "on",
    )


def test_normalize_title_tokens_keeps_words_that_are_not_hosts() -> None:
    assert normalize_title_tokens("checkout latency spike") == (
        "checkout",
        "latency",
        "spike",
    )
