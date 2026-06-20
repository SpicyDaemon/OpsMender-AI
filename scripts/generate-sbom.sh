#!/usr/bin/env bash
set -euo pipefail

if ! command -v syft >/dev/null 2>&1; then
  echo "syft is required: https://github.com/anchore/syft#installation" >&2
  exit 127
fi

output="${1:-sbom.json}"
syft packages dir:. -o cyclonedx-json > "${output}"
echo "Wrote CycloneDX SBOM to ${output}"
