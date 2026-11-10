"""The README and the guides under docs/wiki stay internally consistent
(M1-165): every relative link or image they carry points at a file in the
repository, and none of them uses an em or en dash."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted([ROOT / "README.md", *(ROOT / "docs/wiki").glob("*.md")])
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _relative_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for match in LINK.finditer(path.read_text(encoding="utf-8")):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if target:
            targets.append(target)
    return targets


@pytest.mark.parametrize(
    "page", PAGES, ids=[p.relative_to(ROOT).as_posix() for p in PAGES]
)
def test_every_relative_link_resolves(page: Path):
    missing = [
        t for t in _relative_targets(page) if not (page.parent / t).resolve().exists()
    ]
    assert missing == [], missing


@pytest.mark.parametrize(
    "page", PAGES, ids=[p.relative_to(ROOT).as_posix() for p in PAGES]
)
def test_no_em_or_en_dash(page: Path):
    lines = [
        n
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1)
        if chr(0x2014) in line or chr(0x2013) in line
    ]
    assert lines == [], lines
