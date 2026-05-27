#!/usr/bin/env bash
# Build the `opsmender` single-file binary.
#
# Steps:
#   1. Build the Next.js static export (frontend/out/).
#   2. Install the pyinstaller build group.
#   3. Run pyinstaller with opsmender.spec.
#
# Result: ./dist/opsmender (~50–100 MB depending on platform).
# Run with: ./dist/opsmender serve

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

echo "==> Building frontend static export"
pushd frontend >/dev/null
npm ci
npm run build
popd >/dev/null

echo "==> Installing build dependencies"
uv sync --group build

echo "==> Running PyInstaller"
rm -rf build dist
uv run pyinstaller opsmender.spec

case "${OS:-$(uname -s)}" in
  Windows_NT|MINGW*|MSYS*|CYGWIN*)
    BIN="dist/opsmender.exe"
    ;;
  *)
    BIN="dist/opsmender"
    ;;
esac

echo ""
echo "==> Binary built at: ${BIN}"
echo "==> Test it with: ./${BIN} --version"
