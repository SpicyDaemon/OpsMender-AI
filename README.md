# AI Incident Manager (AIM)

An AI-powered incident response framework with tiered access controls. Connects AI agents to infrastructure via MCP servers and enforces a tier-based permission system that organizations define themselves.

## Quick Start

Requires [uv](https://docs.astral.sh/uv/) (Python package manager) and Python 3.11+.

```bash
# 1. Install dependencies and register the `aim` CLI command
uv sync --dev

# 2. Set up environment
cp .env.example .env        # edit .env with your API keys

# 3. Verify installation
uv run aim --version
uv run aim check             # validates config + MCP connectivity

# 4. Run a dry-run session (no LLM or MCP needed)
uv run aim run --dry-run --incident "High CPU on api-server-01"
```

> **How `aim` works:** `uv sync` installs the project as a Python package, which registers `aim` as a CLI entry point (defined in `pyproject.toml` → `[project.scripts]`). You run it via `uv run aim` or by activating the venv directly (`. .venv/bin/activate && aim`).

### Local Dev Notes

- The repo was verified in a local `.venv` on 2026-04-12 with `266 passed, 2 skipped`.
- `aim approvals ...` requires a reachable database because approval requests are persisted.
- If you are not running Postgres locally yet, SQLite works for local approval-flow testing:

```bash
export AIM_DATABASE_URL="sqlite+aiosqlite:///$(pwd)/aim.db"
```

- If `aim` ever fails with `ModuleNotFoundError: cli` after venv activation, reinstall the package into the venv as a regular wheel:

```bash
.venv/bin/pip install --force-reinstall --no-deps .
```

- After that non-editable install, source changes will not automatically appear in the `aim` launcher until you reinstall again.

## Running Tests

```bash
uv run pytest              # all tests (266 passed, 2 skipped)
uv run pytest -xvs         # verbose, stop on first failure
uv run pytest tests/test_api.py       # API layer tests
uv run pytest tests/test_workflow.py  # workflow tests
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `aim` | Show help |
| `aim --version` | Show version |
| `aim check` | Validate config and test MCP server connectivity |
| `aim run --incident "desc"` | Run a full incident response session |
| `aim run --dry-run --incident "desc"` | Dry-run (no LLM, no MCP) |
| `aim run --tier 2 --incident "desc"` | Override tier level |
| `aim audit` | View the audit log (human-readable table) |
| `aim audit --last N` | Show the last N audit entries |
| `aim audit --session ID` | Filter audit entries by session ID |
| `aim audit --json` | Output audit entries as raw JSONL |
| `aim config` | Show current configuration summary |
| `aim config --json` | Output config as JSON |
| `aim config --validate` | Validate the current configuration |
| `aim approvals list` | List approval requests |
| `aim approvals approve ID` | Approve a pending Tier 1 request |
| `aim approvals reject ID` | Reject a pending Tier 1 request |

## API Server

Sprint 8 introduced a FastAPI REST + WebSocket layer. To start the API server (requires PostgreSQL):

```bash
export AIM_DATABASE_URL="postgresql+asyncpg://aim:aim@localhost:5432/aim"
export AIM_JWT_SECRET="your-secret-key"
uv run uvicorn backend.api.app:create_app --factory --reload
```

### API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/health` | — | Health check |
| `POST` | `/auth/register` | — | Register a new user |
| `POST` | `/auth/login` | — | Log in, receive JWT |
| `GET` | `/auth/me` | Bearer | Current user profile |
| `POST` | `/incidents` | admin/operator | Create incident |
| `GET` | `/incidents` | any | List incidents (pagination, status filter) |
| `GET` | `/incidents/{id}` | any | Get single incident |
| `POST` | `/sessions` | admin/operator | Start a session |
| `GET` | `/sessions/{id}` | any | Get session details |
| `GET` | `/approvals` | any | List approval requests |
| `POST` | `/approvals/{id}/approve` | admin/operator | Approve pending request |
| `POST` | `/approvals/{id}/reject` | admin/operator | Reject pending request |
| `GET` | `/audit` | any | Query audit entries (filters + pagination) |
| `GET` | `/config` | admin/operator | Read system config |
| `PUT` | `/config` | admin | Update system config |
| `WS` | `/sessions/{id}/stream?token=JWT` | JWT query param | Live session streaming |

### Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access — create incidents, sessions, update config |
| `operator` | Create incidents and sessions, read config |
| `viewer` | Read-only access to incidents, sessions, audit |

The first registered user is automatically assigned the `admin` role.

## Configuration

Edit `config.yaml` to add MCP servers. Three transport types are supported:

```yaml
mcp_servers:
  # Local process (stdio)
  - name: kubernetes
    transport: stdio
    command: "npx"
    args: ["-y", "@anthropic/mcp-server-k8s"]

  # Server-Sent Events (sse)
  - name: remote-k8s
    transport: sse
    url: "http://mcp.internal:8080/sse"

  # Streamable HTTP (Sourcebot, etc.)
  - name: sourcebot
    transport: http
    url: "https://sb.example.com/api/mcp"
    token: "your-bearer-token"

audit:
  output: ./logs/audit.jsonl

approvals:
  timeout_seconds: 900
```

## Workflow

AIM uses a LangGraph-powered incident response workflow:

```
observe → diagnose → plan → tier_gate → execute → verify → summarize
```

| Node | Role | Powered by |
|------|------|------------|
| `observe` | Gather initial observations | LLM |
| `diagnose` | Root cause analysis | LLM |
| `plan` | Propose remediation actions (JSON) | LLM |
| `tier_gate` | Enforce tier/skill permissions | **Programmatic** (never LLM) |
| `execute` | Call MCP tools via audited executor | MCP + audit log |
| `verify` | Assess whether incident is resolved | LLM |
| `summarize` | Generate incident summary | LLM |

The `tier_gate` is a hard programmatic check — it cannot be bypassed by agent reasoning.

## Skill Definitions

Organizations define what's safe, cautious, or destructive in a `SKILL.md` file. See `examples/SKILL.md` for a Kubernetes reference template.

```yaml
operations:
  - tool: get_pods
    classification: safe
  - tool: scale_deployment
    classification: caution
  - tool: "delete_*"
    classification: destructive
```

The tier enforcement layer uses these classifications to permit or block tool calls at runtime. Unknown operations are denied at all tiers (fail-closed).

## Tier System

| Tier | Mode | Destructive Actions |
|------|------|---------------------|
| 3 | Advisory only | AI does not execute anything; human executes manually |
| 2 | Safe operations only | Blocked; AI recommends and human executes manually |
| 1 | Approved execution | Allowed only after human approval, then AI executes |
| 0 | Experimental autonomous | Allowed with no approval; non-production/sandbox only |

### Tier 1 Approval Flow

Sprint 9 added a persisted approval flow for destructive Tier 1 actions:

1. The workflow reaches `tier_gate`.
2. A destructive action at Tier 1 creates an `approval_request`.
3. The session moves to `awaiting_approval`.
4. A human approves or rejects via API or `aim approvals`.
5. If approved, AIM executes the action.
6. If rejected or expired, the action is blocked.

Default approval timeout is 15 minutes (`approvals.timeout_seconds: 900`).

## Project Structure

```
ai-incident-manager/
├── backend/
│   ├── agent/              # LangGraph workflow, nodes, state, LLM interface
│   ├── api/                # FastAPI REST + WebSocket layer
│   │   ├── app.py          # App factory (lifespan, CORS, routes)
│   │   ├── auth.py         # JWT auth, bcrypt hashing, RBAC dependencies
│   │   ├── deps.py         # DB session dependency injection
│   │   ├── schemas.py      # Pydantic request/response models
│   │   └── routes/         # Route modules (auth, incidents, sessions, approvals, audit, config, ws)
│   ├── approvals/          # Tier 1 approval service and wait/timeout logic
│   ├── audit/              # JSONL audit logger + PgAuditLogger + audited executor
│   ├── config_loader.py    # YAML config → typed dataclasses
│   ├── db/                 # SQLAlchemy models, async repos, Alembic migrations
│   ├── mcp/                # MCP client wrapper (stdio, sse, http)
│   ├── skills/             # Skill definition parser (SKILL.md)
│   └── tiers/              # Tier enforcement layer
├── cli/
│   └── aim.py              # CLI entry point (run, check, audit, config)
├── examples/
│   └── SKILL.md            # Reference Kubernetes skill definition
├── tests/                  # 266 tests, 2 skipped
├── config.yaml             # Default configuration
└── docs/                   # Project documentation
```

## Progress

- **Phase 1 (Sprints 1–6):** ✅ Complete — CLI, MCP, skills, tiers, audit, LangGraph workflow
- **Phase 2 (Sprints 7–12):** In progress
  - Sprint 7: ✅ Database layer (SQLAlchemy + Alembic + async repos)
  - Sprint 8: ✅ FastAPI REST + WebSocket layer (JWT auth, RBAC, all CRUD endpoints)
  - Sprint 9: ✅ Tier 1 approval flow
  - Sprint 10: ⬜ BYOM provider abstraction
  - Sprint 11: ⬜ Next.js frontend
  - Sprint 12: ⬜ Polish + binary build

## Distribution (Planned — Sprint 12)

The end goal is a standalone binary (via PyInstaller or Nuitka) that requires only two files to run:

```
aim                  # standalone binary
config.yaml          # MCP servers, tier, audit config
.env                 # API keys, database URL, JWT secret
```

No Python install, no `uv`, no virtualenv — just download, configure, and run. This is planned for Sprint 12.

See `docs/REFERENCE.md` for full architecture details.
