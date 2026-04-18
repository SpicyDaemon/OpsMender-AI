"""Tests for Sprint 16 — Node.js bundling in Docker image.

These tests parse the production Dockerfile to verify that Node.js,
npm, and npx are provisioned correctly in the runtime stage.
They do NOT build the Docker image (that requires Docker Engine) —
they validate the Dockerfile *structure* statically, which is fast
and CI-friendly.

To verify the actual built image, run:

    docker build -f docker/Dockerfile -t aim-test .
    docker run --rm aim-test node --version
    docker run --rm aim-test npx --version
"""

import pathlib
import re

import pytest

# Repo root is one level above the tests/ directory.
_REPO = pathlib.Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "docker" / "Dockerfile"


class TestDockerfileNodeProvisioning:
    """Static analysis of docker/Dockerfile to check Node.js is bundled."""

    @pytest.fixture(autouse=True)
    def _load_dockerfile(self):
        assert _DOCKERFILE.is_file(), f"Dockerfile not found at {_DOCKERFILE}"
        self.content = _DOCKERFILE.read_text()

    def test_node_runtime_stage_exists(self):
        """A dedicated stage pulls the Node image for copying binaries."""
        assert re.search(
            r"FROM\s+node:\d+-slim\s+AS\s+node-runtime",
            self.content,
        ), "Missing 'FROM node:XX-slim AS node-runtime' stage"

    def test_node_binary_copied_to_runtime(self):
        """The node binary is COPY'd from the node-runtime stage."""
        assert re.search(
            r"COPY\s+--from=node-runtime\s+/usr/local/bin/node\s+/usr/local/bin/node",
            self.content,
        ), "Node binary not copied from node-runtime stage"

    def test_npm_modules_copied_to_runtime(self):
        """npm's node_modules are COPY'd from the node-runtime stage."""
        assert re.search(
            r"COPY\s+--from=node-runtime\s+/usr/local/lib/node_modules\s+/usr/local/lib/node_modules",
            self.content,
        ), "node_modules not copied from node-runtime stage"

    def test_npm_symlink_created(self):
        """An npm symlink is created in the runtime stage."""
        assert "npm-cli.js" in self.content and "ln -s" in self.content, (
            "npm symlink not created"
        )

    def test_npx_symlink_created(self):
        """An npx symlink is created in the runtime stage."""
        assert "npx-cli.js" in self.content and "ln -s" in self.content, (
            "npx symlink not created"
        )

    def test_four_build_stages(self):
        """The Dockerfile has exactly four FROM stages."""
        stages = re.findall(r"^FROM\s+", self.content, re.MULTILINE)
        assert len(stages) == 4, f"Expected 4 stages, found {len(stages)}"

    def test_node_stage_uses_slim_variant(self):
        """The node-runtime stage uses the slim (Debian) variant, not alpine.

        python:3.12-slim is Debian bookworm; alpine uses musl instead of
        glibc so copied binaries would segfault.
        """
        match = re.search(r"FROM\s+(node:\S+)\s+AS\s+node-runtime", self.content)
        assert match, "node-runtime stage not found"
        image = match.group(1)
        assert "slim" in image, f"node-runtime should use -slim variant, got {image}"
        assert "alpine" not in image, (
            f"node-runtime must NOT use alpine (glibc mismatch), got {image}"
        )
