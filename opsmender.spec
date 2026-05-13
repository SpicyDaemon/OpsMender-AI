# PyInstaller spec for the `opsmender` binary.
#
# Produces a single-file executable that bundles:
#   * Python runtime + all backend source
#   * The Next.js static export from frontend/out/
#   * Alembic migrations + alembic.ini
#   * skills/ directory (auto-imported on first run)
#   * examples/SKILL.md (reference template)
#
# Build locally:
#   uv sync --group build
#   uv run pyinstaller opsmender.spec
#
# The resulting ./dist/opsmender takes `opsmender serve` to start the full app.

# ruff: noqa
# pylint: skip-file
# mypy: ignore-errors

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

import os

datas = [
    ("frontend/out", "frontend/out"),
    ("alembic.ini", "."),
    ("backend/db/migrations", "backend/db/migrations"),
    ("examples/SKILL.md", "examples"),
]
# `skills/` is optional — only present if an operator has dropped built-in
# skill definitions into it before building.
if os.path.isdir("skills"):
    datas.append(("skills", "skills"))

# Alembic loads the migration env.py dynamically; PyInstaller can't follow
# those imports statically, so we collect the tree.
def _skip_mcp_cli(name: str) -> bool:
    # mcp.cli pulls in typer which we don't ship; nothing in Opsmender uses it.
    return not name.startswith("mcp.cli")


hiddenimports = []
hiddenimports += collect_submodules("alembic")
hiddenimports += collect_submodules("asyncpg")
hiddenimports += collect_submodules("aiosqlite")
hiddenimports += collect_submodules("langgraph")
hiddenimports += collect_submodules("mcp", filter=_skip_mcp_cli)
hiddenimports += collect_submodules("backend")

a = Analysis(
    ["cli/opsmender.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="opsmender",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
