# Contributing to OpsMender AI

Thanks for your interest in OpsMender. This guide covers the local setup, the test loop, and how we review pull requests.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Local setup

OpsMender has a Python backend (FastAPI + LangGraph) and a Next.js static export for the dashboard.

### Prerequisites

- Python 3.11+
- Node.js 24+ (the Docker image bundles one — needed locally only if you want to run MCP servers that ship as `npx` packages)
- [`uv`](https://docs.astral.sh/uv/) for Python dependency management
- Docker (optional — for running the full stack with Postgres)

### Install

```bash
# backend
uv sync --dev

# frontend
cd frontend && npm install
```

### Run the stack locally

```bash
# backend API (http://localhost:8000) with a file-backed SQLite DB
uv run python scripts/dev_server.py

# frontend dev server (http://localhost:3000) — proxies /api to :8000
cd frontend && npm run dev
```

Or use Docker Compose:

```bash
docker compose -f docker/docker-compose.yml up --build
```

## Test loop

### Backend

```bash
# full suite (SQLite, no Postgres required)
uv run python -m pytest -q

# target a single module while you iterate
uv run python -m pytest tests/test_api.py -q

# integration tests that hit a real K8s cluster + MCP server
uv run python -m pytest -m integration
```

### Frontend

```bash
cd frontend
npm run build      # must pass — this is what ships in the Docker image
npm run lint
```

### Full pre-PR check

```bash
uv run python -m pytest -q
cd frontend && npm run build && cd ..
```

Both must pass before a PR is opened.

## Branch + commit conventions

- Branch name: `feature/<short-slug>`, `fix/<short-slug>`, or `docs/<short-slug>`
- One logical change per PR. Split unrelated refactors into their own PRs.
- Commit messages: imperative mood, short subject, optional body explaining the *why*.

## Pull requests

Before you open a PR:

1. Both the backend suite and the frontend build pass locally.
2. User-visible changes have an entry queued for the next `CHANGELOG.md` release section.
3. New features have tests. New bug fixes include a regression test.
4. If you touched the session workflow, tier gate, or audit log, re-read `docs/PROMPT_CONTEXT.md` — those areas have hard architectural constraints documented there.

Use the PR template. Reviewers will focus on:

- Correctness of tier/skill enforcement (never bypass)
- Test coverage for new behavior
- Audit log completeness for any new side-effect
- API / CLI / UI backwards compatibility

## Architecture guardrails

OpsMender has a few deliberate invariants — please read [docs/PROMPT_CONTEXT.md](docs/PROMPT_CONTEXT.md) before proposing changes in these areas:

- **Tier gate is programmatic.** It cannot be bypassed by agent reasoning.
- **MCP-first.** No provider-specific integrations for infrastructure access.
- **Skill definitions are org-owned.** The framework never edits or overrides a `SKILL.md`.
- **Audit on everything.** Every tool call and every state transition is logged.

Proposed changes that conflict with a locked decision in `docs/PROMPT_CONTEXT.md` should be raised as an issue first — they need a design discussion before code.

## Reporting security issues

Do **not** open a public issue. See [SECURITY.md](SECURITY.md) for the reporting process.
