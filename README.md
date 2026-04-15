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

### Runtime Inputs

In practice, an AIM deployment is driven by four operator-owned inputs:

- `.env` for deployment defaults such as tier, audit path, DB/JWT settings, provider defaults, and local fallbacks
- `runtime_config` DB overrides for UI-editable runtime settings such as tier and log level
- `model_configs` and `mcp_servers` DB tables for saved model profiles and MCP connection definitions managed through the API/UI
- `skills/` for your environment-specific `SKILL.md` files that define what counts as safe, caution, or destructive

This is intentional: AIM does not hardcode what "destructive" means for your infrastructure. The operator defines that through skills.

### Local Dev Notes

- The repo was verified in a local `.venv` on 2026-04-14 with `328 passed, 2 skipped`.
- `aim approvals ...` requires a reachable database because approval requests are persisted.
- `aim config model set ...` also requires a reachable database because model configs are persisted.
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
uv run pytest              # all tests (328 passed, 2 skipped)
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
| `aim config model list` | Discover provider availability and reported models |
| `aim config model set --provider ... --model-id ...` | Validate and persist the default model config |
| `aim approvals list` | List approval requests |
| `aim approvals approve ID` | Approve a pending Tier 1 request |
| `aim approvals reject ID` | Reject a pending Tier 1 request |

## Running Locally (Full Stack)

AIM is split into two services that run independently in development:

- **Backend** — FastAPI on `http://localhost:8000` (REST + WebSocket + auth + DB)
- **Frontend** — Next.js on `http://localhost:3000` (UI only; calls the backend via `NEXT_PUBLIC_API_URL`)

Both services must be running for the UI to work. Use two terminal tabs.

### Terminal 1 — Backend

From the **project root**:

```bash
uv run python scripts/dev_server.py
```

This launcher is the easiest way to run AIM locally — it loads `.env`, uses the shared DB fallback chain (`AIM_DATABASE_URL` → local Postgres → SQLite), seeds an `admin` / `admin123` user, and starts Uvicorn on port 8000.

Sanity check:

```bash
curl http://localhost:8000/docs -o /dev/null -w "%{http_code}\n"   # expect 200
```

### Terminal 2 — Frontend

From the **`frontend/` directory**:

```bash
npm install      # first time only
npm run dev
```

The frontend reads `frontend/.env.local` (`NEXT_PUBLIC_API_URL=http://localhost:8000`). Open `http://localhost:3000` and log in with `admin` / `admin123`.

### Shutting down cleanly

Always stop both services with **Ctrl-C** — do not just close the terminal tab. A detached Next.js or Uvicorn process will keep holding its port and the next `npm run dev` / dev_server will either fail or silently exit.

If a previous run got orphaned:

```bash
lsof -i :3000              # find the PID holding port 3000
kill <PID>                 # or: kill -9 <PID> if it ignores SIGTERM
lsof -i :8000              # same check for the backend
```

### Production-style backend (Postgres)

For running against Postgres instead of the dev SQLite DB:

```bash
export AIM_DATABASE_URL="postgresql+asyncpg://aim:aim@localhost:5432/aim"
export AIM_JWT_SECRET="your-secret-key"
uv run alembic upgrade head
uv run uvicorn backend.api.app:create_app --factory --reload
```

Note: the ASGI target is `backend.api.app:create_app` **with `--factory`** — there is no `backend.api.main` module.

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
| `GET` | `/models` | any | Discover provider availability and reported models |
| `GET` | `/models/configs` | any | List saved model configs |
| `POST` | `/models/configs` | admin | Create saved model config |
| `PUT` | `/models/configs/{id}` | admin | Update saved model config |
| `DELETE` | `/models/configs/{id}` | admin | Delete saved model config |
| `POST` | `/models/configs/{id}/set-default` | admin | Mark saved model config as default |
| `GET` | `/mcp-servers` | any | List saved MCP servers |
| `POST` | `/mcp-servers` | admin | Create saved MCP server |
| `PUT` | `/mcp-servers/{id}` | admin | Update saved MCP server |
| `DELETE` | `/mcp-servers/{id}` | admin | Delete saved MCP server |
| `POST` | `/mcp-servers/{id}/test` | admin | Test live connectivity to a saved MCP server |
| `GET` | `/audit` | any | Query audit entries (filters + pagination) |
| `GET` | `/config` | admin/operator | Read system config |
| `PUT` | `/config` | admin | Update system config |
| `PUT` | `/config/model` | admin | Validate and persist the default model config |
| `WS` | `/sessions/{id}/stream?token=JWT` | JWT query param | Live session streaming |

### Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access — create incidents, sessions, update config |
| `operator` | Create incidents and sessions, read config |
| `viewer` | Read-only access to incidents, sessions, audit |

The first registered user is automatically assigned the `admin` role.

## Configuration

Runtime defaults now live in `.env`, and UI edits to tier/log level are persisted in the `runtime_config` table.
Saved model profiles and MCP server definitions are also persisted in the database, with `.env` remaining the source of truth for deployment defaults and secrets.

Example `.env` keys:

```dotenv
AIM_TIER=2
AIM_LOG_LEVEL=INFO
AIM_AUDIT_LOG=./logs/audit.jsonl
AIM_APPROVAL_TIMEOUT_SECONDS=900
AIM_SKILL_DEFINITION=./skills/production/SKILL.md
AIM_DATABASE_URL=postgresql+asyncpg://aim:aim@localhost:5432/aim
AIM_JWT_SECRET=change-me-in-production
```

`AIM_SKILL_DEFINITION` should point to an operator-owned `SKILL.md` file. Different environments can use different skill files, for example:

```text
skills/
├── production/SKILL.md
├── staging/SKILL.md
└── sandbox/SKILL.md
```

## Model Providers

Sprint 10 added a provider abstraction layer for:

- Anthropic
- OpenAI
- Azure OpenAI
- Ollama

You can inspect provider availability from the CLI:

```bash
aim config model list
aim config model list --provider ollama --base-url http://localhost:11434
```

You can persist the default model config into the database:

```bash
aim config model set --provider openai --model-id gpt-4o --api-key-env-var OPENAI_API_KEY
aim config model set --provider azure_openai --model-id my-deployment \
  --base-url https://example-resource.openai.azure.com/ \
  --api-version 2024-10-21 \
  --api-key-env-var AZURE_OPENAI_API_KEY
aim config model set --provider ollama --model-id llama3.2 --base-url http://localhost:11434
```

Notes:

- `aim config model list` is discovery-only and does not write to the database.
- `aim config model set` validates the provider config first, then stores it in `model_configs` and marks it as default.
- If you want to run a local Hugging Face model with AIM, the clean path is to serve it through a local runtime such as Ollama or another OpenAI-compatible endpoint rather than loading raw checkpoints directly inside AIM.

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

Organizations define what's safe, cautious, or destructive in a `SKILL.md` file. This is one of the core design constraints of AIM: the framework enforces the skill definition you provide rather than deciding destructiveness itself.

That means users can bring their own skills to match their environment. For example, deleting a pod in production might be classified as `destructive`, while the same action in a sandbox environment might be treated differently.

See `examples/SKILL.md` for a Kubernetes reference template.

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

This is how AIM supports operator-defined destructive actions: your `SKILL.md` files define the policy boundary for your environment, and AIM enforces that boundary programmatically.

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

Default approval timeout is 15 minutes (`AIM_APPROVAL_TIMEOUT_SECONDS=900`).

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
│   │   └── routes/         # Route modules (auth, incidents, sessions, approvals, audit, config, models, mcp_servers, ws)
│   ├── approvals/          # Tier 1 approval service and wait/timeout logic
│   ├── audit/              # JSONL audit logger + PgAuditLogger + audited executor
│   ├── config_loader.py    # .env/AppConfig loader + typed dataclasses
│   ├── db/                 # SQLAlchemy models, async repos, Alembic migrations
│   ├── llm/                # Provider abstraction, registry, and factories
│   ├── mcp/                # MCP client wrapper (stdio, sse, http)
│   ├── skills/             # Skill definition parser (SKILL.md)
│   └── tiers/              # Tier enforcement layer
├── cli/
│   └── aim.py              # CLI entry point (run, check, audit, config, approvals)
├── examples/
│   └── SKILL.md            # Reference Kubernetes skill definition
├── skills/                 # Operator-owned environment skill files
├── tests/                  # 328 tests, 2 skipped
├── .env                    # Deployment defaults / secrets / local fallbacks
└── docs/                   # Project documentation
```

## Progress

- **Phase 1 (Sprints 1–6):** ✅ Complete — CLI, MCP, skills, tiers, audit, LangGraph workflow
- **Phase 2 (Sprints 7–13):** In progress
  - Sprint 7: ✅ Database layer (SQLAlchemy + Alembic + async repos)
  - Sprint 8: ✅ FastAPI REST + WebSocket layer (JWT auth, RBAC, all CRUD endpoints)
  - Sprint 9: ✅ Tier 1 approval flow
  - Sprint 10: ✅ BYOM provider abstraction
  - Sprint 11: ✅ Next.js frontend + Docker setup
  - Sprint 12: 🚧 Config consolidation + UI self-service (foundation, model manager, and MCP backend foundation complete; dynamic MCP reload, frontend MCP manager, skills, and chat next)
  - Sprint 13: ⬜ Polish + binary build

## Distribution (Planned — Sprint 13)

The end goal is a standalone binary (via PyInstaller or Nuitka) that requires only two files to run:

```
aim                  # standalone binary
.env                 # deployment defaults, API keys, database URL, JWT secret
skills/              # operator-owned skill definitions for each environment
```

No Python install, no `uv`, no virtualenv — just download, configure, and run. This is planned for Sprint 13.

See `docs/REFERENCE.md` for full architecture details.
