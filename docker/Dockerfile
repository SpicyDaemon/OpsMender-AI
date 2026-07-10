# OpsMender AI — single container (frontend + backend)
#
# Four stages:
#   1. frontend-builder  — Node 24, produces /app/frontend/out (static export)
#   2. node-runtime      — Node 24 slim, provides node/npm/npx for the runtime
#   3. backend-builder   — Python 3.12, installs deps into /app/.venv via uv
#   4. runtime           — slim Python image, copies venv + Node + static export + source
#
# Result: one image, one port (8000), one process (uvicorn).
# FastAPI serves /api/* (all existing routes) and falls through to the static
# Next build for everything else. Node.js is on PATH so npx-based MCP servers
# (e.g. `npx -y @anthropic/mcp-server-k8s`) work out of the box.

# ── Stage 1: frontend (static export) ───────────────────────────────────────
FROM node:24-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# Same-origin API calls by default; override at build time if the frontend
# will be hosted on a different origin than the backend.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build


# ── Stage 2: Node.js runtime (for npx-based MCP servers) ────────────────────
# Same Debian bookworm base as python:3.12-slim → glibc-compatible binaries.
FROM node:24-slim AS node-runtime

# Upgrade the bundled npm so its own vendored dependencies (e.g. picomatch,
# flagged by Trivy as CVE-2026-33671) are patched before this stage's global
# node_modules is copied into the runtime image. npm here only backs npx-based
# MCP servers; it is not on the application request path.
RUN npm install -g npm@latest


# ── Stage 3: backend (Python deps) ──────────────────────────────────────────
FROM python:3.12-slim AS backend-builder

WORKDIR /app

# Build deps for xmlsec / lxml (required by python3-saml). Build-time only —
# the runtime stage installs the runtime libxmlsec1 / libxml2 packages.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libxml2-dev \
        libxmlsec1-dev \
        libxmlsec1-openssl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project


# ── Stage 4: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# System deps: curl for healthcheck, tini as PID 1 for clean shutdown,
# libxml2 / libxmlsec1 for python3-saml (Sprint 30).
# `apt-get upgrade` pulls the latest base-image security patches (e.g. the
# libssh2 deb13u1 fixes flagged by the 2026-07-03 trivy scan) on top of
# whatever the pinned python:3.12-slim tag shipped with.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
        curl \
        tini \
        libxml2 \
        libxmlsec1 \
        libxmlsec1-openssl \
 && rm -rf /var/lib/apt/lists/*

# Node.js — copied from the node-runtime stage so npx-based MCP servers
# (e.g. `npx -y @anthropic/mcp-server-k8s`) work without the user having
# to install Node separately.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Python venv
COPY --from=backend-builder /app/.venv /app/.venv

# Backend source + CLI + alembic
COPY backend/ ./backend/
COPY cli/ ./cli/
COPY alembic.ini ./alembic.ini
COPY skills/ ./skills/
COPY examples/ ./examples/

# Static frontend produced in stage 1
COPY --from=frontend-builder /app/frontend/out /app/frontend/out

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPSMENDER_FRONTEND_STATIC_DIR=/app/frontend/out

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health/ready || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["sh", "-c", "\
  python -m alembic upgrade head && \
  exec uvicorn backend.api.app:create_app --factory --host 0.0.0.0 --port 8000 \
"]
