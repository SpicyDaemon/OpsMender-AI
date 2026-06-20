#!/usr/bin/env bash
set -euo pipefail

# Container-signing scaffold
# --------------------------
# The release pipeline should build exactly once in an isolated GitHub-hosted
# runner, publish an immutable digest, attach build provenance, and sign that
# digest. Combined with protected source and a non-falsifiable hosted build,
# that is the intended SLSA Level 3 workflow. This script is the manual/keyed
# fallback; CI should prefer keyless OIDC signing and provenance attestations.
#
# Manual use:
#   export COSIGN_KEY=/secure/path/cosign.key
#   export COSIGN_PASSWORD='...'
#   scripts/sign-image.sh ghcr.io/example/opsmender@sha256:<digest>
#
# Verification:
#   cosign verify --key /secure/path/cosign.pub <image>@sha256:<digest>

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign is required: https://docs.sigstore.dev/cosign/system_config/installation/" >&2
  exit 127
fi

image="${1:-}"
if [[ -z "${image}" ]]; then
  echo "usage: $0 <image>@sha256:<digest>" >&2
  exit 2
fi
if [[ "${image}" != *@sha256:* ]]; then
  echo "refusing to sign a mutable tag; pass an image digest" >&2
  exit 2
fi
if [[ -z "${COSIGN_KEY:-}" ]]; then
  echo "COSIGN_KEY must point to a Cosign private key" >&2
  exit 2
fi

cosign sign --yes --key "${COSIGN_KEY}" "${image}"
