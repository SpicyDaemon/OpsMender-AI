"""No em or en dashes in anything the product ships (owner rule, 2026-09-26).

They read as machine-written. Use a period, comma, colon or parentheses in
prose, and a plain hyphen in code comments and ranges. Code that has to
recognise the glyph in model output writes it as an escape.

The tier gate is frozen for this release, so it is the one exception for now.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GLYPHS = (chr(0x2014), chr(0x2013))  # em dash, en dash
ALLOWED = {"backend/tiers/enforcement.py"}
BINARY = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
}


def _tracked_files() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return listing.stdout.splitlines()


def test_no_em_or_en_dashes_in_tracked_files():
    offenders = []
    for rel in _tracked_files():
        if rel in ALLOWED or pathlib.Path(rel).suffix.lower() in BINARY:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(glyph in line for glyph in GLYPHS):
                offenders.append(f"{rel}:{number}: {line.strip()[:80]}")
    assert not offenders, (
        "Use plain punctuation instead of em/en dashes:\n" + "\n".join(offenders[:40])
    )
