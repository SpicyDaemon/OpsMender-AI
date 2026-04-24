#!/usr/bin/env bash
# Copy generated brand assets into the proper project locations.
# Run from the project root after placing the generated PNGs.
#
# Usage:
#   bash scripts/copy_brand_assets.sh <favicon_png> <og_image_png>
#
# Example:
#   bash scripts/copy_brand_assets.sh /tmp/aim_favicon.png /tmp/aim_og_image.png

set -euo pipefail

FAVICON_SRC="${1:?Usage: $0 <favicon_png> <og_image_png>}"
OG_SRC="${2:?Usage: $0 <favicon_png> <og_image_png>}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Copying logo.png to frontend/public/"
cp "$FAVICON_SRC" "$PROJECT_ROOT/frontend/public/logo.png"

echo "==> Copying og-image.png to frontend/public/"
cp "$OG_SRC" "$PROJECT_ROOT/frontend/public/og-image.png"

echo "==> Done. Assets placed at:"
echo "    frontend/public/logo.png"
echo "    frontend/public/og-image.png"
echo ""
echo "Note: favicon.ico at frontend/app/favicon.ico should be manually"
echo "converted from the logo PNG using a tool like realfavicongenerator.net"
echo "or: convert logo.png -resize 48x48 favicon.ico"
