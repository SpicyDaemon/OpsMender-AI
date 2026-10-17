"""M1-67 (O-24): every action the workflows use is pinned to a major that runs
on Node 24, and Node 24 is the minimum everywhere the repository names a Node
version (.nvmrc, the frontend's engines, the workflows, the image)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))

# The majors whose action.yml says `using: node24`, checked on 2026-10-16.
# Raise an entry when a newer verified major arrives; never lower one.
NODE24_MAJORS = {
    "actions/checkout": 7,
    "actions/setup-python": 7,
    "actions/setup-node": 7,
    "actions/upload-artifact": 7,
    "actions/download-artifact": 8,
    "actions/upload-pages-artifact": 5,
    "actions/deploy-pages": 5,
    "astral-sh/setup-uv": 10,
    "docker/setup-buildx-action": 4,
    "docker/build-push-action": 7,
    "github/codeql-action/init": 4,
    "github/codeql-action/analyze": 4,
    "softprops/action-gh-release": 3,
}
# A composite action runs shell steps; the actions it pins inside are its own.
COMPOSITE = {"aquasecurity/trivy-action"}


def _uses() -> list[tuple[str, str]]:
    found = []
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"uses:\s*(\S+)", line)
            if match and not match.group(1).startswith("./"):
                found.append((path.name, match.group(1)))
    return found


def test_every_action_is_a_node_24_release():
    seen = set()
    for workflow, ref in _uses():
        action, _, version = ref.partition("@")
        seen.add(action)
        if action in COMPOSITE:
            continue
        assert action in NODE24_MAJORS, f"{workflow}: {ref} is not on the Node 24 list"
        match = re.fullmatch(r"v(\d+)(?:\.\d+)*", version)
        assert match, f"{workflow}: {ref} is not pinned to a major"
        assert int(match[1]) >= NODE24_MAJORS[action], (
            f"{workflow}: {ref} is below the Node 24 major v{NODE24_MAJORS[action]}"
        )
    assert set(NODE24_MAJORS) <= seen, sorted(set(NODE24_MAJORS) - seen)


def test_the_postgres_concurrency_step_has_room_to_finish():
    """The hosted PostgreSQL step runs about five minutes; its ceiling must stay
    well above that, or a passing run is killed at the finish line (M1-71)."""
    verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    step = verify.split("- name: Postgres concurrency tests", 1)[1].split("- name:", 1)[
        0
    ]
    match = re.search(r"timeout-minutes:\s*(\d+)", step)
    assert match and int(match[1]) >= 12, step


def test_node_24_is_the_minimum_everywhere():
    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "24"
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    assert package["engines"]["node"] == ">=24"
    versions = [
        (path.name, line.strip())
        for path in WORKFLOWS
        for line in path.read_text(encoding="utf-8").splitlines()
        if "node-version" in line
    ]
    assert versions, "no workflow names a Node version"
    for workflow, line in versions:
        assert re.fullmatch(r"node-version:\s*['\"]?24['\"]?", line), (workflow, line)
    dockerfile = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM node:24-", dockerfile, re.M)
