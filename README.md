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
- `model_configs`, `mcp_servers`, and `skills` DB tables for saved model profiles, MCP connection definitions, and operator-owned skill definitions — all managed through the API/UI
- `skills/` directory for your environment-specific `SKILL.md` files that define what counts as safe, caution, or destructive. Files placed here are auto-imported into the `skills` DB table on backend startup (existing rows are skipped by name, so UI edits are preserved across restarts). `examples/SKILL.md` is a reference template only and is never auto-imported.

This is intentional: AIM does not hardcode what "destructive" means for your infrastructure. The operator defines that through skills.

### Local Dev Notes

- Sprint 13 verification (`tests/test_e2e.py` + `tests/test_frontend_mount.py`) is green using a file-backed temp SQLite DB. See [End-to-End Verification](#end-to-end-verification) below for how to run it.
- On Python 3.14.x, async SQLite hangs if the engine is opened against an in-memory URL (`sqlite+aiosqlite://`). Use a file URL (`sqlite+aiosqlite:///path/to/file.db`) — the E2E fixture already does this via `tmp_path`.
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
uv run pytest              # full suite
uv run pytest -xvs         # verbose, stop on first failure
uv run pytest tests/test_api.py       # API layer tests
uv run pytest tests/test_workflow.py  # workflow tests
```

### End-to-End Verification

`tests/test_e2e.py` is the canonical "does AIM still work?" check — it drives the full single-container chain that operators actually use, with no external services touched:

```
register/login → POST /incidents → POST /sessions → tier gate creates approval
            → POST /approvals/{id}/approve (or /reject) → gate resumes
            → mocked MCP call → PgAuditLogger writes rows → GET /audit
```

`tests/test_frontend_mount.py` covers the static frontend mount + SPA fallback served by FastAPI (the same path the binary and Docker image use).

Run both together:

```bash
uv run pytest tests/test_e2e.py tests/test_frontend_mount.py -v
```

Expected: 9 passed (2 E2E flows — approve + reject — plus 7 frontend mount cases).

What it actually exercises end-to-end:
- JWT auth + RBAC (admin vs operator vs viewer)
- Incident + session creation through the REST API
- LangGraph tier gate persisting an `approval_request` and waiting on it
- `POST /approvals/{id}/approve` and `/reject` resuming the gate
- `PgAuditLogger` writing `tool_call_start` + `tool_call_end` rows for every executed tool
- `GET /audit?entry_type=tool_call_end` returning the post-execution view an operator sees
- Frontend static export mounted at `/` with SPA fallback for unknown paths

Notes:
- The fixture uses a file-backed `tmp_path/e2e.db` SQLite file. **Do not switch it to `sqlite+aiosqlite://` (in-memory)** — that hangs on Python 3.14.x.
- MCP is mocked (`AsyncMock`); no real cluster or network is contacted.
- The `SKILL_MD` constant must stay in YAML frontmatter form (matching `examples/SKILL.md`) — the parser ignores markdown bullet lists, which would silently make every action "unknown" and break the gate.

If you change anything in the API layer, the workflow, the approval service, the audit logger, or the static frontend mount, this is the test that proves the whole chain still composes.

## CLI Commands

| Command | Description |
|---------|-------------|
| `aim` | Show help |
| `aim --version` | Show version |
| `aim check` | Validate config and test MCP server connectivity |
| `aim serve` | Start the API and embedded static frontend |
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

AIM runs on a **single port (8000)** — backend API + static frontend export served by one Python process. There is no separate frontend port.

> **Known limitation:** the Alembic migrations use Postgres-specific types, so `aim serve` currently only works against Postgres. The dev server (`scripts/dev_server.py`) sidesteps Alembic by using `Base.metadata.create_all`, which is dialect-aware and works on SQLite.

### Dev mode (recommended)

One terminal, one port. Loads `.env`, follows the DB fallback chain (`AIM_DATABASE_URL` → local Postgres → SQLite `aim-local.db`), seeds `admin` / `admin123`, and starts Uvicorn on port 8000 serving both the API and the embedded static frontend.

```bash
# 1. Build the static frontend (only when the frontend changes)
cd frontend && npm install && npm run build && cd ..

# 2. Start the app
uv run python scripts/dev_server.py
```

Open **http://localhost:8000** and log in with `admin` / `admin123`.

When you change frontend code, run `cd frontend && npm run build && cd ..` and restart the dev server.

### Production mode (`aim serve` with Postgres)

```bash
# 1. Start Postgres (one-time)
docker run -d --name aim-pg \
  -e POSTGRES_USER=aim -e POSTGRES_PASSWORD=aim -e POSTGRES_DB=aim \
  -p 5432:5432 postgres:16

# 2. Point AIM at it
echo "AIM_DATABASE_URL=postgresql+asyncpg://aim:aim@localhost:5432/aim" >> .env

# 3. Build the static frontend (only when the frontend changes)
cd frontend && npm install && npm run build && cd ..

# 4. Start the app
uv run aim serve
```

Open **http://localhost:8000** → click **Register** → first registered user becomes admin automatically.

### Raw Uvicorn (alternative)

```bash
export AIM_DATABASE_URL="postgresql+asyncpg://aim:aim@localhost:5432/aim"
export AIM_JWT_SECRET="your-secret-key"
uv run alembic upgrade head
uv run uvicorn backend.api.app:create_app --factory --reload
```

Note: the ASGI target is `backend.api.app:create_app` **with `--factory`** — there is no `backend.api.main` module.

### Shutting down cleanly

Always stop with **Ctrl-C**. If a previous run got orphaned:

```bash
lsof -i :8000              # find the PID holding the port
kill <PID>                 # or: kill -9 <PID> if it ignores SIGTERM
```



## Distribution

Sprint 13 closed out the single-container distribution path:

- `aim serve` starts the FastAPI API and the embedded static frontend from one Python process
- `docker/Dockerfile` builds a single container image that serves both backend and frontend on port `8000`
- `docker/docker-compose.yml` runs the app with Postgres, health checks, and a logs volume
- `aim.spec` plus `scripts/build_binary.sh` define the PyInstaller path for a standalone `aim` binary

Build the binary locally with:

```bash
./scripts/build_binary.sh
./dist/aim --version
./dist/aim serve
```

Verified end-to-end via `tests/test_e2e.py` + `tests/test_frontend_mount.py` (see [End-to-End Verification](#end-to-end-verification)).

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
| `POST` | `/incidents/ingest` | Ingest token | Webhook — ingest incident from external source |
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
| `GET` | `/skills` | any | List saved skills (optional `?mcp_server_id=` filter) |
| `GET` | `/skills/{id}` | any | Get a saved skill |
| `POST` | `/skills` | admin | Create a saved skill |
| `PUT` | `/skills/{id}` | admin | Update a saved skill |
| `DELETE` | `/skills/{id}` | admin | Delete a saved skill |
| `POST` | `/skills/{id}/clone` | admin | Clone a saved skill (optionally rebind to MCP server) |
| `POST` | `/skills/import` | admin | Upload and import a `SKILL.md` file |
| `GET` | `/audit` | any | Query audit entries (filters + pagination) |
| `GET` | `/config` | admin/operator | Read system config |
| `PUT` | `/config` | admin | Update system config |
| `PUT` | `/config/model` | admin | Validate and persist the default model config |
| `GET` | `/sessions/{id}/messages` | any | List co-pilot chat messages |
| `POST` | `/sessions/{id}/messages` | admin/operator | Send user message to co-pilot |
| `GET` | `/ingest-tokens` | admin | List ingest tokens |
| `POST` | `/ingest-tokens` | admin | Create ingest token (returns raw token once) |
| `POST` | `/ingest-tokens/{id}/revoke` | admin | Revoke (deactivate) an ingest token |
| `DELETE` | `/ingest-tokens/{id}` | admin | Permanently delete an ingest token |
| `GET` | `/ingest-providers` | any | List available ingest provider adapters |
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

MCP servers are resolved through a dynamic pool (`backend/mcp/pool.py`) that re-reads the DB on every lookup — servers added via `POST /mcp-servers` or the dashboard are visible to already-running sessions with no reload. `AIM_MCP_SERVERS_JSON` stays supported as a read-only fallback for bootstrapping before any DB entries exist.

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

### Skill Manager (UI + DB)

As of Sprint 12 Feature 3, skills are also managed from the dashboard:

- `/dashboard/skills` groups skills by MCP server with a "Global (unassigned)" section for the fallback skill.
- Admins can **Import** `.md` files, **Clone** a skill to a different MCP server, and create/edit/delete skills inline.
- Skills in `skills/` are auto-imported on backend startup — existing rows are skipped by name, so edits made in the UI are preserved across restarts.
- Enforcement looks up the skill bound to the session's MCP server first, then falls back to the global (unassigned) skill. If neither exists, behavior falls back to file-path loading via `AIM_SKILL_DEFINITION`.

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

## External Incident Ingestion

Sprints 14 + 15 added a webhook-based ingestion system that lets external monitoring/alerting tools create incidents in AIM automatically.

Sprint 15's **universal (`auto`) adapter** is now the default: a single endpoint accepts any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Prometheus Alertmanager, custom scripts — without requiring a per-platform adapter. Heuristics match common field names and envelopes first; unrecognized shapes fall back to an LLM that returns the field **paths** (cached per-token by a shape hash so the same payload shape pays the LLM cost only once).

### How It Works

1. An admin creates an **ingest token** via `POST /ingest-tokens`, specifying which provider adapter to use (default: `auto`).
2. The raw token (starts with `aim_ingest_...`) is returned **once** — save it. AIM stores only the SHA-256 hash.
3. External systems send JSON payloads to `POST /incidents/ingest` with the token in an `X-AIM-Token` header (or `Authorization: Bearer`).
4. AIM routes the payload through the chosen adapter (`auto` for universal, or a strict shape-specific adapter), which normalizes it into an incident.
5. For `auto` tokens: heuristic parse → LLM fallback on unrecognized shapes → per-token shape cache so the same payload shape skips the LLM next time. Admins can pre-train via `sample_payload` at creation, or `POST /ingest-tokens/{id}/learn-shape` later.
6. Dedup by `(external_source, external_id)` — repeated alerts update or skip instead of creating duplicates. `auto` tokens scope `external_source = "auto:<token-name>"` so cross-token ID collisions don't merge.
7. Every inbound payload is logged raw in the `ingest_log` table for replay/debugging.
8. Per-token rate limiting enforced (default: 60 req/min). Returns `429` with `Retry-After` header when exceeded.
9. Optional auto-start can create one session automatically for newly created incidents that match a configured source + minimum severity rule.

**Rate limit config** (in `.env`):
```dotenv
AIM_INGEST_RATE_LIMIT=60     # max requests per window per token (0 = disabled)
AIM_INGEST_RATE_WINDOW=60    # window size in seconds
```

**Optional ingest auto-start** (env defaults, also editable in `/dashboard/config`):
```dotenv
AIM_INGEST_AUTO_START_ENABLED=false
AIM_INGEST_AUTO_START_MIN_SEVERITY=critical
AIM_INGEST_AUTO_START_SOURCE=
```

When enabled, AIM auto-creates a single session only for newly created incidents whose `external_source` matches the configured source filter (or any source if blank) and whose severity is at or above the configured threshold. The new session inherits the current runtime tier, and duplicate ingests do not spawn extra active sessions.

### Supported Provider Adapters

| Provider | Key | Handles |
|----------|-----|---------|
| **Universal (auto-detect)** | `auto` | **Default.** Any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Alertmanager, custom scripts. Heuristics + LLM fallback with per-token shape cache. |
| CloudWatch | `cloudwatch` | SNS `SubscriptionConfirmation` + `Notification` envelopes with embedded alarm JSON |
| Azure Monitor | `azure_monitor` | Common alert schema v2 — maps severity (Sev0–4) and monitor condition |
| LegacyAlertVendor | `legacy_alert_vendor` | v2 webhooks — `incident.triggered`, `.acknowledged`, `.resolved` |
| LegacyAlertRelay | `legacy_alert_relay` | Webhook integration payloads — `Create`, `Acknowledge`, `Close`, and update-style alert actions |
| Generic JSON | `generic` | Configurable dot-path field mapping — works with tools needing strict, deterministic parsing |

### Quick Test (curl)

```bash
# 1. Get an admin JWT
TOKEN=$(curl -s http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Create an ingest token (default provider = auto, accepts any JSON)
INGEST=$(curl -s http://localhost:8000/ingest-tokens \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"test-source","provider":"auto"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 3. Send an incident
curl -s http://localhost:8000/incidents/ingest \
  -H "X-AIM-Token: $INGEST" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Disk Full","description":"98% on /data","severity":"high","id":"alert-001"}'
# → {"success":true,"dedup_action":"created",...}

# 4. Same payload again → dedup kicks in
curl -s http://localhost:8000/incidents/ingest \
  -H "X-AIM-Token: $INGEST" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Disk Full","description":"98% on /data","severity":"high","id":"alert-001"}'
# → {"success":true,"dedup_action":"skipped",...}
```

For full curl recipes covering all five providers (CloudWatch SNS, Azure Monitor, LegacyAlertVendor, LegacyAlertRelay, Generic), including lifecycle examples and severity mapping tables, see [`docs/REFERENCE.md`](docs/REFERENCE.md#external-incident-ingestion).

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
│   │   └── routes/         # Route modules (auth, incidents, sessions + chat, approvals, audit, config, models, mcp_servers, skills, ws, ingest)
│   ├── chat/               # Async co-pilot chat responder (parallel LLM call + WS push)
│   ├── ingest/             # External incident ingestion (Sprint 14)
│   │   ├── adapters/       # Provider adapters (cloudwatch, azure_monitor, legacy_alert_vendor, legacy_alert_relay, generic)
│   │   ├── registry.py     # Adapter registry (provider key → adapter class)
│   │   └── service.py      # Token auth, adapter dispatch, dedup, audit logging
│   ├── detector/           # MCP-driven detector runner + scheduler + templates (Sprint 14)
│   ├── approvals/          # Tier 1 approval service and wait/timeout logic
│   ├── audit/              # JSONL audit logger + PgAuditLogger + audited executor
│   ├── config_loader.py    # .env/AppConfig loader + typed dataclasses
│   ├── db/                 # SQLAlchemy models, async repos, Alembic migrations
│   ├── llm/                # Provider abstraction, registry, and factories
│   ├── mcp/                # MCP client wrapper (stdio/sse/http) + dynamic server pool
│   ├── skills/             # Skill definition parser (SKILL.md) + startup auto-importer
│   └── tiers/              # Tier enforcement layer
├── cli/
│   └── aim.py              # CLI entry point (run, check, audit, config, approvals)
├── examples/
│   └── SKILL.md            # Reference Kubernetes skill definition
├── skills/                 # Operator-owned environment skill files (auto-imported on startup)
├── tests/                  # full unit + integration suite, plus tests/test_e2e.py + tests/test_frontend_mount.py for the single-container chain
├── .env                    # Deployment defaults / secrets / local fallbacks
└── docs/                   # Project documentation
```

## Progress

- **Phase 1 (Sprints 1–6):** ✅ Complete — CLI, MCP, skills, tiers, audit, LangGraph workflow
- **Phase 2 (Sprints 7–14):** In progress
  - Sprint 7: ✅ Database layer (SQLAlchemy + Alembic + async repos)
  - Sprint 8: ✅ FastAPI REST + WebSocket layer (JWT auth, RBAC, all CRUD endpoints)
  - Sprint 9: ✅ Tier 1 approval flow
  - Sprint 10: ✅ BYOM provider abstraction
  - Sprint 11: ✅ Next.js frontend + Docker setup
  - Sprint 12: ✅ Config consolidation + UI self-service (foundation, model manager, dynamic MCP pool, `/dashboard/config` MCP manager, Skill Manager `/dashboard/skills`, Co-pilot Chat)
  - Sprint 13: ✅ Single-container app — `aim serve` + unified `docker/Dockerfile` + PyInstaller binary, E2E + frontend-mount verification green
  - Sprint 14: 🔧 External incident ingestion — core API + 5 provider adapters + dedup + ingest audit log + admin UI + curl recipes + rate limiting done; MCP-driven detector backend + templates + dashboard UI done; optional auto-start remains

## Distribution Status

The current target is a standalone binary plus operator-owned config/assets:

```
aim                  # standalone binary
.env                 # deployment defaults, API keys, database URL, JWT secret
skills/              # operator-owned skill definitions for each environment
```

The repo ships the PyInstaller spec/build script and the unified `aim serve` entrypoint; the full chain (auth → incident → session → approval → execute → audit, plus the static frontend mount) is covered by `tests/test_e2e.py` and `tests/test_frontend_mount.py`.

See `docs/REFERENCE.md` for full architecture details.
