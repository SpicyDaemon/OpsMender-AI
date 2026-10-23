"""M1-72 and M1-81: the lock stays at or above the releases that closed the
advisories the dependency audit found after v1.2.0 (Pillow 13.0.0, certifi
2026.10.19) and after v1.2.1 (black 26.10.1)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Package -> the first release without the advisories; raise, never lower.
FLOORS = {"pillow": (13, 0, 0), "certifi": (2026, 10, 19), "black": (26, 10, 1)}


def _locked_version(name: str) -> tuple[int, ...]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    [package] = [row for row in lock["package"] if row["name"] == name]
    return tuple(int(part) for part in re.findall(r"\d+", package["version"]))


def test_locked_releases_stay_above_the_advisory_floors():
    for name, floor in FLOORS.items():
        assert _locked_version(name) >= floor, name


def test_the_pillow_floor_is_declared_in_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"Pillow>=13\.0\.0"', pyproject)


def test_the_black_floor_is_declared_in_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"black>=26\.10\.1"', pyproject)
