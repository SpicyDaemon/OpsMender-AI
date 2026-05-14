"""One-shot rename: AIM/aim → OpsMender/opsmender across the repo.

Applies word-boundary regex substitutions to every text file under the repo
(skipping vendored / build / cache dirs). Run once, then delete this script.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".next", ".pytest_cache", ".mypy_cache",
    "__pycache__", "dist", "build", "out", "htmlcov", ".claude", ".codex",
    ".gemini", ".anthropic", "logs",
}

SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff",
    ".woff2", ".ttf", ".otf", ".eot", ".pyc", ".pyo", ".exe", ".dll",
    ".so", ".dylib", ".db", ".sqlite", ".sqlite3", ".zip", ".tar", ".gz",
    ".jar", ".class", ".lock",
}

# Order matters: longer / more-specific patterns first.
SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AI Incident Manager"), "OpsMender AI"),
    (re.compile(r"\bAIM_"), "OPSMENDER_"),
    (re.compile(r"\bAIM\b"), "OpsMender"),
    (re.compile(r"\baim_"), "opsmender_"),
    (re.compile(r"\baim-"), "opsmender-"),
    (re.compile(r"\baim:"), "opsmender:"),
    (re.compile(r"\baim/"), "opsmender/"),
    (re.compile(r"\baim\."), "opsmender."),
    (re.compile(r"\baim\b"), "opsmender"),
]


def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in SKIP_EXTS:
        return True
    # Skip the rename script itself.
    if path.resolve() == Path(__file__).resolve():
        return True
    return False


def rewrite(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False
    new = text
    for pat, repl in SUBS:
        new = pat.sub(repl, new)
    if new != text:
        path.write_text(new, encoding="utf-8", newline="")
        # Preserve original line endings by re-reading the source if needed
        return True
    return False


def main() -> None:
    changed: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        if rewrite(path):
            changed.append(path.relative_to(ROOT))
    print(f"Rewrote {len(changed)} files.")
    for p in changed:
        print(f"  {p}")


if __name__ == "__main__":
    main()
