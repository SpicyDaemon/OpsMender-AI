"""Resource-path helpers for running inside a PyInstaller bundle.

PyInstaller extracts bundled data to ``sys._MEIPASS`` at startup. To keep the
rest of the code oblivious we:

1. Resolve ``resource_path("frontend/out")`` to the bundled copy when frozen,
   otherwise to the repo-relative path.
2. ``bootstrap_bundled_env()`` sets ``AIM_FRONTEND_STATIC_DIR`` (and similar
   path-like env vars) to the bundled locations when frozen, before the
   config loader runs. Nothing else in the codebase has to know about
   PyInstaller.
"""

from __future__ import annotations

import os
import pathlib
import sys


def is_frozen() -> bool:
    """True when running inside a PyInstaller onefile/onedir bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_root() -> pathlib.Path:
    """Directory that bundled data files live in.

    * Frozen: ``sys._MEIPASS``
    * Source: the repository root (parent of this module's parent).
    """
    if is_frozen():
        return pathlib.Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return pathlib.Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> pathlib.Path:
    """Resolve a relative path against the bundle or source tree."""
    return resource_root() / relative


def bootstrap_bundled_env() -> None:
    """Point env vars at bundled resources when running as a frozen binary.

    Only sets a variable if the caller hasn't already provided one — user
    overrides (``--env-file``, explicit ``export AIM_...=...``) always win.
    """
    if not is_frozen():
        return

    root = resource_root()

    defaults = {
        "AIM_FRONTEND_STATIC_DIR": str(root / "frontend" / "out"),
        "AIM_SKILL_DEFINITION": str(root / "examples" / "SKILL.md"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    # Alembic uses a config-file path rather than env; the serve command
    # resolves it via ``resource_path("alembic.ini")`` directly.
