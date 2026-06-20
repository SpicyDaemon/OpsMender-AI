#!/usr/bin/env bash
set -euo pipefail

if ! command -v trivy >/dev/null 2>&1; then
  echo "trivy is required: https://trivy.dev/latest/getting-started/installation/" >&2
  exit 127
fi

image="${1:-opsmender:local}"
trivy image --config trivy.yaml "${image}"
