# OpsMender AI

[![Website](https://img.shields.io/badge/website-opsmenderai.com-blue)](https://opsmenderai.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/SpicyDaemon/OpsMender-AI?include_prereleases&sort=semver)](https://github.com/SpicyDaemon/OpsMender-AI/releases)

> **OpsMender AI** — open-source **AI incident manager** / **AI SRE** / **AI on-call** for production infrastructure. Tier-gated, MCP-first, human-in-the-loop AI incident response and incident management.

An AI-powered incident response framework with tiered access controls. Connects AI agents to infrastructure via MCP servers and enforces a tier-based permission system that organizations define themselves.

*Keywords: AI incident manager, AI incident management, AI incident response, AI SRE, AI on-call, agentic incident response, LangGraph incident response, MCP runbook automation.*

🌐 **[opsmenderai.com](https://opsmenderai.com)** | 📚 **[Read the Documentation Wiki](docs/wiki/README.md)** | 🛠 **[Developer Architecture & API Reference](docs/REFERENCE.md)**

## Why OpsMender

- **MCP-first** — every infrastructure action goes through an MCP server the operator provides. No native integrations locked to one cloud or tool.
- **Tiered autonomy** — four tiers from advice-only (Tier 3) to fully autonomous (Tier 0). Tier 0 has a sandbox, hard time limits, and automatic rollback.
- **Human in the loop** — Tier 1 pauses the workflow on destructive actions and requires explicit approval from an operator or admin.
- **Programmatic tier gate** — enforced in code, not by prompt. The agent cannot reason its way past it.
- **Org-owned skill definitions** — a single `SKILL.md` classifies every operation as `safe`, `caution`, or `destructive`. Your call, not ours.
- **Full audit log** — every node transition, every tool call, every approval, every rollback step.
- **Bring your own model** — Anthropic, OpenAI, Azure OpenAI, or local Ollama.
- **Universal ingest** — accept webhooks from CloudWatch, Azure Monitor, GCP Cloud Monitoring, Oracle Cloud (OCI), LegacyAlertVendor, LegacyAlertRelay, Grafana, Datadog, Slack, or anything else that POSTs JSON.
- **Outbound triggers** — fire session-lifecycle notifications to Slack, Teams, Sumo Logic, or any generic webhook endpoint.
- **Multi-tenant** — strict per-org isolation across every entity. Optional host-based routing pins each tenant to its own URL (`acme.opsmender.example.com`, `globex.opsmender.example.com`) with custom branding.
- **Per-tenant SSO** — each org can wire its own **OIDC** identity provider (Okta, Azure AD, Google Workspace, Auth0, Keycloak) **or SAML 2.0** IdP (older Okta, ADFS, classic Azure AD enterprise apps). Users are JIT-provisioned on first login; OIDC client secrets are encrypted at rest, SAML uses a global SP keypair from env. Local login stays available as a break-glass path.

## The full incident-response loop

OpsMender's job is one cohesive loop: **alert → AI → ack → fix → resolve.** Every paged incident walks the same five stages.

```
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                │
   │  1. ALERT FIRES                                                │
   │     Prometheus / Datadog / CloudWatch / Azure Monitor /        │
   │     LegacyAlertVendor / Slack alerts / anything-that-POSTs-JSON        │
   │     hits /incidents/ingest with a service-scoped token.        │
   │                                                                │
   │  2. AI STARTS WORKING                                          │
   │     Priority rule decides P0/P1/P2/P3 + response mode          │
   │     (auto_resolve / notify / page / escalate_immediate).       │
   │     LangGraph workflow runs the tier-gated session in          │
   │     parallel — Tier 0 fixes autonomously, Tier 1 pauses on     │
   │     destructive actions, Tier 2 stays read-only, Tier 3        │
   │     advises only.                                              │
   │                                                                │
   │  3. OPERATOR ACKS                                              │
   │     Page mode → escalation chain fires step 0; on-call user    │
   │     gets a Slack DM / Teams DM / Email / SMS with             │
   │     Acknowledge / Take Over / Resolve buttons. Click           │
   │     Acknowledge (or run /ack in chat) — chain pauses,          │
   │     incident assignment created.                               │
   │                                                                │
   │  4. INCIDENT FIXED                                             │
   │     Either the AI auto-resolves it (Tier 0/1 executes a        │
   │     remediation plan, you approve any Tier 1 actions through   │
   │     the dashboard or chat), or the operator takes over and     │
   │     drives the fix themselves.                                 │
   │                                                                │
   │  5. RESOLUTION                                                 │
   │     Click Resolve (in chat or web UI). Chain cancels,          │
   │     incident.status → resolved, the full audit trail (every    │
   │     node transition, tool call, approval, rollback step) is    │
   │     preserved for postmortem.                                  │
   │                                                                │
   └────────────────────────────────────────────────────────────────┘
```

The end-to-end loop is exercised by `tests/test_e2e_paging_flow.py::TestIncidentResponseLoop` — that test walks the entire flow through real HTTP routes (ingest → priority → page → chain → Slack ack → web force-takeover → Slack resolve) and ships as the canonical regression guard for the paging surface.

For the operator-facing walkthrough — services / teams / rosters / escalation chains / priority rules / response modes / maintenance windows / notification preferences — see [docs/wiki/paging-guide.md](docs/wiki/paging-guide.md). For platform-specific chat-surface details, see the [Slack](docs/wiki/slack-paging-surface.md) and [Teams](docs/wiki/teams-paging-surface.md) guides.

## How OpsMender thinks (concepts in 60 seconds)

The loop above is the operator's view. Underneath, four configurable surfaces drive the behavior:

### Ingest — *getting alerts in (stage 1 of the loop)*

OpsMender's core job is to take alerts your existing monitoring already fires and respond to them intelligently. Your monitoring tools (Prometheus Alertmanager, Datadog, CloudWatch, Azure Monitor, Sumo Logic, Grafana, third-party automation suites, anything that can POST JSON) send to `/incidents/ingest`; OpsMender creates an incident from the payload and runs the tier-gated AI response workflow. The **universal adapter** accepts any shape and asks the LLM to extract the title/severity/description on first sight, then caches the path mapping per token (so a Datadog payload only costs an LLM call once). Typed adapters exist for CloudWatch SNS, Azure Monitor, GCP Monitoring, OCI, LegacyAlertVendor, LegacyAlertRelay, and a Generic JSON adapter for stricter parsing.

> **This is what 90% of operators set up first.** Bring your existing alerts; OpsMender responds. Incidents can also be created manually from the dashboard for the cases where monitoring missed something and an operator wants to attach a session + audit trail to an ad-hoc investigation.

### Paging — *deciding who gets pinged (stage 3 of the loop)*

OpsMender owns paging end-to-end — you don't bolt LegacyAlertVendor on top. Configure **services**, **teams**, **rosters** (with deterministic on-call rotation in IANA time zones), **escalation chains** (additive — once paged, stay paged, with a hard 15-minute inactivity timeout), and **priority rules** (first-match-wins on the alert payload) under `/dashboard/paging`. Operators set per-user **notification preferences** (which channels, priority routing matrix, quiet hours) under `/dashboard/paging` → My Notifications. **Maintenance windows** suppress paging in a time range (scoped global / service / roster / team). The chain engine and notification dispatcher run as background loops with restart-safe watermarks.

> **Wire this once per service.** Walkthrough lives in [docs/wiki/paging-guide.md](docs/wiki/paging-guide.md).

### Workflows — *the order of the autonomous response steps (stage 2 of the loop)*

When a session runs, OpsMender walks a LangGraph: `observe → diagnose → plan → tier_gate → execute → verify → summarize`. A **Workflow profile** lets you save a *different* node order — same nodes, just rearranged or trimmed. The tier gate must always sit immediately before `execute` (programmatic safety floor; cannot be moved or removed).

> **Most operators never touch this.** It's there for the rare team that wants, e.g., an extra `verify` pass after `summarize`. The default workflow is fine for 95% of use cases.

### Agent teams — *which specialist personas reason about the problem*

Inside the reasoning nodes (`diagnose`, `plan`), instead of one generic LLM pass, you can configure multiple specialist roles to each take a pass — "SRE", "Database engineer", "Security engineer" — followed by a synthesis pass that merges their views. Saved as **Agent team profiles**.

> **Most operators never touch this either.** Default is one generic persona. Useful for orgs that want, e.g., a security-leaning bias on every plan, or a DBA perspective baked into every diagnosis.

### Webhook triggers — *getting events out*

Whenever a session changes state (`created`, `awaiting_approval`, `active`, `completed`, `failed`, `timed_out`), OpsMender POSTs a payload to whatever URLs you've configured. Format presets exist for **Slack** incoming webhooks, **Teams** workflow webhooks, **Sumo Logic**, or **generic JSON**.

> **This is what you set up if you want OpsMender to ping your chat / SIEM / pager.** Most teams configure one Slack webhook and stop there. Distinct from Bot connectors below — webhook triggers are fire-and-forget event notifications, bot connectors are two-way command/chat surfaces.

### Where each concept lives in the dashboard

The Config page groups settings by how often you'll touch them:

| Group | Frequency | What's in it |
|-------|-----------|--------------|
| **Day-1 setup** (must do once) | Always | Models, MCP servers, Skills |
| **Inbound** (alerts → OpsMender) | Most operators | Ingest tokens |
| **Outbound** (OpsMender → people/systems) | Most operators | Webhook triggers, Bot connectors |
| **Advanced** (defaults work for 95%) | Rarely | Workflows, Agent teams |

If you're new to OpsMender, work top-down: get one model + one MCP server + one skill definition working (Day-1), then wire your monitoring to Ingest, then configure one paging service + roster + chain (`/dashboard/paging` — see the [Paging Guide](docs/wiki/paging-guide.md)) so on-call operators actually get pinged. Workflows and Agent teams can wait until you have a concrete reason to touch them.

> **Scheduled environment checks:** The legacy Detector surface has been retired. Use **Environment Scans** (`/dashboard/scans`) for on-demand sweeps, `POST /audits/schedules` for recurring read-only scans, and `opsmender detectors-migrate --apply` before running the detector-drop migration if an older deployment still has `detector_rules` rows.

## Quick Start

Requires [uv](https://docs.astral.sh/uv/) (Python package manager) and Python 3.11+.

```bash
# 1. Install dependencies and register the `opsmender` CLI command
uv sync --dev

# 2. Create your .env (SQLite works out of the box — no Postgres needed)
cp .env.example .env

# 3. Verify installation
uv run opsmender --version
uv run opsmender run --dry-run --incident "High CPU on api-server-01"   # no LLM/MCP needed
```

See [Running the dev server](#running-the-dev-server) to start the full API + dashboard locally.

> **How `opsmender` works:** `uv sync` installs the project as a Python package, which registers `opsmender` as a CLI entry point (defined in `pyproject.toml` → `[project.scripts]`). Run it via `uv run opsmender` or by activating the venv directly (`. .venv/bin/activate && opsmender`).

### Runtime Inputs

In practice, an OpsMender deployment is driven by four operator-owned inputs:

- `.env` for deployment defaults such as tier, audit path, DB/JWT settings, provider defaults, and local fallbacks
- `runtime_config` DB overrides for UI-editable runtime settings such as tier and log level
- `model_configs`, `mcp_servers`, and `skills` DB tables for saved model profiles, MCP connection definitions, and operator-owned skill definitions — all managed through the API/UI
- `skills/` directory for your environment-specific `SKILL.md` files that define what counts as safe, caution, or destructive. Files placed here are auto-imported into the `skills` DB table on backend startup (existing rows are skipped by name, so UI edits are preserved across restarts). `examples/SKILL.md` is a reference template only and is never auto-imported.

This is intentional: OpsMender does not hardcode what "destructive" means for your infrastructure. The operator defines that through skills.

## Running Tests

```bash
uv run pytest              # full suite
uv run pytest -xvs         # verbose, stop on first failure
uv run pytest tests/test_api.py       # API layer tests
uv run pytest tests/test_workflow.py  # workflow tests
```

### End-to-End Verification

`tests/test_e2e.py` is the canonical "does OpsMender still work?" check — it drives the full single-container chain that operators actually use, with no external services touched:

```
register/login → POST /incidents → POST /sessions → tier gate creates approval
            → POST /approvals/{id}/approve (or /reject) → gate resumes
            → mocked MCP call → PgAuditLogger writes rows → GET /audit
```

`tests/test_e2e_paging_flow.py` is the canonical paging-loop check (Sprint 40 step 1). It drives one cohesive test through real HTTP routes: an inbound alert hits `/incidents/ingest` (service-scoped token) → the priority rule fires (`severity: critical → P1 + page`) → the escalation chain is selected and started → a signed Slack `block_actions` ACK on `/bot/slack/interactions` pauses the chain and assigns the operator → an admin's `POST /incidents/{id}/take {force: true}` swaps the assignment via the web UI → a signed Slack ACTION_RESOLVE cancels the chain and flips `incidents.status` to `resolved`. The channel factory yields `None` for every channel in the test env, so the dispatcher writes the audit-anchor `incident_pages` row without attempting delivery — exactly the production path on a deployment with no configured channels.

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
| `opsmender` | Show help |
| `opsmender --version` | Show version |
| `opsmender check` | Validate config and test MCP server connectivity |
| `opsmender serve` | Start the API and embedded static frontend |
| `opsmender run --incident "desc"` | Run a full incident response session |
| `opsmender run --dry-run --incident "desc"` | Dry-run (no LLM, no MCP) |
| `opsmender run --tier 2 --incident "desc"` | Override tier level |
| `opsmender audit` | View the audit log (human-readable table) |
| `opsmender audit --last N` | Show the last N audit entries |
| `opsmender audit --session ID` | Filter audit entries by session ID |
| `opsmender audit --json` | Output audit entries as raw JSONL |
| `opsmender config` | Show current configuration summary |
| `opsmender config --json` | Output config as JSON |
| `opsmender config --validate` | Validate the current configuration |
| `opsmender config model list` | Discover provider availability and reported models |
| `opsmender config model set --provider ... --model-id ...` | Validate and persist the default model config |
| `opsmender config model bootstrap` | First-run bootstrap for the default model config (prompts or flags) |
| `opsmender approvals list` | List approval requests |
| `opsmender approvals approve ID` | Approve a pending Tier 1 request |
| `opsmender approvals reject ID` | Reject a pending Tier 1 request |

## Running the dev server

OpsMender runs on a **single port (8000)** — backend API + static frontend served by one Python process. The dev launcher (`scripts/dev_server.py`) sidesteps Alembic by using `Base.metadata.create_all`, which works on SQLite. It loads `.env`, creates the schema, seeds `admin` / `admin123`, and starts Uvicorn serving both the API and the embedded static frontend.

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager) and Python 3.11+
- Node.js 20+ and npm (for the frontend build)
- No Postgres required — the dev server falls back to local SQLite when Postgres isn't reachable

### One-time setup

```bash
# 1. Install Python deps and register the `opsmender` CLI command
uv sync --dev

# 2. Create your .env from the template
cp .env.example .env
#    Then open .env and either:
#    - leave OPSMENDER_DATABASE_URL commented out (SQLite fallback at ./opsmender-local.db), or
#    - set OPSMENDER_DATABASE_URL=sqlite+aiosqlite:///./opsmender-local.db explicitly.
#    Provider keys (Anthropic / OpenAI / Azure / Ollama) can be added later
#    via the dashboard or directly in .env.

# 3. Build the static frontend (creates frontend/out/ that FastAPI serves)
cd frontend && npm install && npm run build && cd ..
```

Files created by the steps above:
- `.env` — your local copy of `.env.example`
- `opsmender-local.db` — SQLite database, created automatically on first dev-server start
- `frontend/out/` — static Next.js export served by the FastAPI catch-all route

### Start the server

```bash
uv run python scripts/dev_server.py
```

Open **http://localhost:8000** and log in with `admin` / `admin123`.
If no default model config exists yet, go to **Config → Models** and bootstrap one from the dashboard before running live sessions.

### Hot-reload workflow (frontend iteration)

For fast frontend changes, skip the static build and run the Next.js dev server on port 3000 alongside the backend on 8000:

```bash
# one-time: tell the Next.js dev server where the API lives
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > frontend/.env.local

# terminal 1 — backend only
uv run python scripts/dev_server.py

# terminal 2 — frontend dev server (hot reload)
cd frontend && npm run dev
```

Then open **http://localhost:3000**. The frontend calls the API on `:8000` using the env var above.

> **Why the `.env.local` step matters:** `frontend/lib/api.ts` defaults `BASE_URL` to same-origin so the production single-process build (`opsmender serve` on `:8000`) works out of the box. In dev with two separate processes, the frontend on `:3000` would otherwise call itself for `/auth/login` and 404. The env var routes calls to the backend. `frontend/.env.local` is gitignored, so each clone needs its own.

### Common issues

- **`GET / → 404 Not Found`** in the dev-server log: `frontend/out/` doesn't exist. Run `cd frontend && npm install && npm run build` first, then restart the dev server.
- **Login or any API call returns 404 from `http://localhost:3000`** (hot-reload setup): `frontend/.env.local` is missing or doesn't set `NEXT_PUBLIC_API_URL`. The frontend is calling itself instead of the backend. Create the file (see *Hot-reload workflow* above) and restart `npm run dev` — Next.js only loads env files at startup.
- **CSS / `@theme` token changes don't appear after hot-reload (Tailwind v4 + Turbopack):** stop `npm run dev`, `rm -rf frontend/.next`, then restart. Turbopack occasionally caches the generated utility classes from a stale `@theme` block.
- **`Connect call failed ('127.0.0.1', 5432)`** at startup: your `.env` still has `OPSMENDER_DATABASE_URL=postgresql+asyncpg://…` but no Postgres is running. Comment that line out (or set it to a SQLite URL) so the SQLite fallback engages.
- **Code changes not picked up:** `dev_server.py` runs Uvicorn with `reload=False`. Stop it (`Ctrl+C`) and restart after edits to backend code.
- **Port 8000 in use:** `lsof -i :8000` to find the PID, then `kill <PID>`.

---

## Running in production

### `opsmender serve` with Postgres

`opsmender serve` runs Alembic migrations, which use Postgres-specific types, so it requires Postgres.

```bash
# 1. Start Postgres (one-time)
docker run -d --name opsmender-pg \
  -e POSTGRES_USER=opsmender -e POSTGRES_PASSWORD=opsmender -e POSTGRES_DB=opsmender \
  -p 5432:5432 postgres:16

# 2. Point OpsMender at it in .env
OPSMENDER_DATABASE_URL=postgresql+asyncpg://opsmender:opsmender@localhost:5432/opsmender

# 3. Build the static frontend (only when the frontend changes)
cd frontend && npm install && npm run build && cd ..

# 4. Start the app
uv run opsmender serve
```

Open **http://localhost:8000** → click **Register** → first registered user becomes admin automatically.

### Raw Uvicorn (Postgres only)

```bash
export OPSMENDER_DATABASE_URL="postgresql+asyncpg://opsmender:opsmender@localhost:5432/opsmender"
export OPSMENDER_JWT_SECRET="your-secret-key"
uv run alembic upgrade head
uv run uvicorn backend.api.app:create_app --factory --reload
```

The ASGI target is `backend.api.app:create_app` **with `--factory`** — there is no `backend.api.main` module.

### Troubleshooting

- **SQLite on Python 3.14.x:** use a file URL (`sqlite+aiosqlite:///./opsmender-local.db`). In-memory (`sqlite+aiosqlite://`) hangs.
- **`ModuleNotFoundError: cli` when running `opsmender` (macOS + iCloud Desktop):** Python 3.14's `site.py` skips `.pth` files marked with the BSD `hidden` flag, which iCloud Drive sets on everything under a synced `Desktop/` or `Documents/`. That breaks the editable install's sys.path wiring. Fix: `chflags -R nohidden .venv`. Long-term, either exclude `.venv` from iCloud ("Remove Download" on the folder) or keep the project outside an iCloud-synced path.
- **`ModuleNotFoundError: cli` on other setups:** reinstall as a regular wheel — `.venv/bin/pip install --force-reinstall --no-deps .` (note: source changes won't hot-reload in the `opsmender` launcher after a non-editable install).
- **Port 8000 already in use:** `lsof -i :8000` to find the PID, then `kill <PID>`.
- **`opsmender approvals …` or `opsmender config model set …` errors:** both require a reachable DB.



## Distribution

Sprint 13 closed out the single-container distribution path:

- `opsmender serve` starts the FastAPI API and the embedded static frontend from one Python process
- `docker/Dockerfile` builds a single container image that serves both backend and frontend on port `8000` — **Node.js 22 LTS is bundled** so `npx`-based MCP servers (e.g. `@anthropic/mcp-server-k8s`) work out of the box
- `docker/docker-compose.yml` runs the app with Postgres, health checks, and a logs volume
- `opsmender.spec` plus `scripts/build_binary.sh` define the PyInstaller path for a standalone `opsmender` binary

> **Node.js for the binary:** The PyInstaller binary does **not** bundle Node.js. If you use `npx`-based MCP servers, install Node.js LTS on the host and ensure `npx` is on `$PATH`, or set `OPSMENDER_NODE_PATH=/path/to/node/bin` in `.env`.

Build the binary locally with:

```bash
./scripts/build_binary.sh
./dist/opsmender --version
./dist/opsmender serve
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
| `GET` | `/models/bootstrap` | any | Read first-run/default-model bootstrap status |
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
| `GET` | `/bot-connectors` | admin | List external chat bot connectors |
| `POST` | `/bot-connectors` | admin | Create external chat bot connector |
| `PUT` | `/bot-connectors/{id}` | admin | Update external chat bot connector |
| `DELETE` | `/bot-connectors/{id}` | admin | Delete external chat bot connector |
| `POST` | `/bot-connectors/{id}/test` | admin | Validate connector configuration |
| `GET` | `/bot-connectors/platforms` | admin | List supported platforms with their typed form schemas + `oauth_enabled` flag |
| `GET` | `/bot-connectors/platforms/{platform}/schema` | admin | Get one platform's form schema |
| `GET` | `/bot-connectors/oauth/{platform}/start` | admin | Begin Slack/Discord OAuth install — returns `{authorize_url}` |
| `GET` | `/bot-connectors/oauth/{platform}/callback` | signed state | OAuth callback — exchanges code, writes bot token to connector credentials |
| `GET` | `/organizations` | admin | List organizations (multi-tenancy) |
| `POST` | `/organizations` | admin | Create organization |
| `PUT` | `/organizations/{id}` | admin | Update organization (name, slug, branding) |
| `GET` | `/organizations/{id}/domains` | admin | List host-based routing domains for an org |
| `POST` | `/organizations/{id}/domains` | admin | Register a domain for host-based routing |
| `POST` | `/organizations/{id}/domains/{domain_id}/set-primary` | admin | Mark domain as primary |
| `DELETE` | `/organizations/{id}/domains/{domain_id}` | admin | Remove a domain |
| `GET` | `/auth/me/organizations` | any | List orgs the current user belongs to |
| `PUT` | `/auth/me/primary-org/{id}` | any | Set the user's persisted primary org |
| `GET` | `/tenant/resolve` | public | Report whether the request host pins a tenant (also reports OIDC + SAML status) |
| `GET` | `/organizations/{id}/sso` | admin | Read per-org OIDC config (never returns the secret) |
| `PUT` | `/organizations/{id}/sso` | admin | Create/update OIDC config — supply `client_secret` only on create or rotate |
| `DELETE` | `/organizations/{id}/sso` | admin | Disable OIDC for the org |
| `GET` | `/auth/sso/{slug}/login` | public | Initiate the OIDC login flow (302 redirect to the IdP) |
| `GET` | `/auth/sso/{slug}/callback` | public | OIDC callback — JIT-provisions user, hands OpsMender JWT back via URL fragment |
| `GET` | `/organizations/{id}/saml` | admin | Read per-org SAML config (never returns the raw IdP XML) |
| `PUT` | `/organizations/{id}/saml` | admin | Create/update SAML config — exactly one of `idp_metadata_url` or `idp_metadata_xml` required |
| `DELETE` | `/organizations/{id}/saml` | admin | Disable SAML for the org |
| `GET` | `/auth/saml/{slug}/login` | public | Initiate the SAML login flow (302 redirect to the IdP) |
| `POST` | `/auth/saml/{slug}/acs` | public | SAML Assertion Consumer Service — validates response, JIT-provisions user, hands OpsMender JWT back via URL fragment |
| `GET` | `/auth/saml/{slug}/metadata` | public | Return SP metadata XML for IdP admins to upload |
| `POST` | `/bot-connectors/{id}/telegram/webhook` | Telegram secret header | Handle inbound Telegram bot commands |
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
| `GET` | `/webhook-triggers` | admin | List outbound webhook triggers |
| `POST` | `/webhook-triggers` | admin | Create outbound webhook trigger |
| `PUT` | `/webhook-triggers/{id}` | admin | Update outbound webhook trigger |
| `DELETE` | `/webhook-triggers/{id}` | admin | Delete outbound webhook trigger |
| `POST` | `/webhook-triggers/{id}/test` | admin | Send a test outbound webhook payload |
| `GET` | `/workflow-profiles` | any | List saved workflow profiles |
| `POST` | `/workflow-profiles` | admin | Create workflow profile |
| `PUT` | `/workflow-profiles/{id}` | admin | Update workflow profile |
| `DELETE` | `/workflow-profiles/{id}` | admin | Delete workflow profile |
| `GET` | `/agent-team-profiles` | any | List saved agent team profiles |
| `POST` | `/agent-team-profiles` | admin | Create agent team profile |
| `PUT` | `/agent-team-profiles/{id}` | admin | Update agent team profile |
| `DELETE` | `/agent-team-profiles/{id}` | admin | Delete agent team profile |
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

MCP servers are resolved through a dynamic pool (`backend/mcp/pool.py`) that re-reads the DB on every lookup — servers added via `POST /mcp-servers` or the dashboard are visible to already-running sessions with no reload. `OPSMENDER_MCP_SERVERS_JSON` stays supported as a read-only fallback for bootstrapping before any DB entries exist.

External chat bot connectors are managed in **Config -> Integrations** or through the `/bot-connectors` API. Credentials are write-only: API responses expose `credential_keys` and `has_credentials`, never raw token values. OpsMender supports 15 platforms: Telegram, Signal, WhatsApp, Slack, Discord, MS Teams, Mattermost, Matrix, Lark/Feishu, DingTalk, WeCom, WeChat, Twilio, Email, Home Assistant, and BlueBubbles.

Example `.env` keys:

```dotenv
OPSMENDER_TIER=2
OPSMENDER_LOG_LEVEL=INFO
OPSMENDER_AUDIT_LOG=./logs/audit.jsonl
OPSMENDER_APPROVAL_TIMEOUT_SECONDS=900
OPSMENDER_SKILL_DEFINITION=./skills/production/SKILL.md
OPSMENDER_DATABASE_URL=postgresql+asyncpg://opsmender:opsmender@localhost:5432/opsmender
OPSMENDER_JWT_SECRET=change-me-in-production
```

### Notification channels (Sprint 35)

When the escalation engine pages a user, it fans out across the channels the user enabled in **My Notifications**. Per-deployment credentials for those channels are read from env:

```dotenv
# Slack DM
OPSMENDER_SLACK_BOT_TOKEN=xoxb-...

# Teams DM (MessageCard webhook; Graph API is Sprint 37)
OPSMENDER_TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...

# Email
OPSMENDER_SMTP_HOST=smtp.example.com
OPSMENDER_SMTP_PORT=587
OPSMENDER_SMTP_USER=opsmender
OPSMENDER_SMTP_PASSWORD=...
OPSMENDER_SMTP_FROM=opsmender@example.com
OPSMENDER_SMTP_USE_TLS=true

# SMS (Twilio)
OPSMENDER_TWILIO_ACCOUNT_SID=AC...
OPSMENDER_TWILIO_AUTH_TOKEN=...
OPSMENDER_TWILIO_FROM_NUMBER=+15550000000

# Public URL — used to deep-link from Slack page cards back to the web UI
OPSMENDER_PUBLIC_URL=https://opsmender.example.com
```

Channels with missing credentials are silently skipped — the dispatcher writes an `incident_pages` row with `delivery_status='skipped'` and reason `channel_unconfigured` so operators can see in the audit log which channel was unavailable.

### Slack interactivity (Sprint 36)

Page cards delivered via `slack_dm` ship with Acknowledge / Take Over / Resolve buttons and a "View in OpsMender" deep-link. Wire it up:

1. In your Slack app, enable **Interactivity & Shortcuts** and set the **Request URL** to `https://<your-opsmender-host>/bot/slack/interactions`.
2. Under **Slash Commands**, register `/ack`, `/take`, `/release`, `/resolve`, `/snooze`, and `/status`, each with the **Request URL** `https://<your-opsmender-host>/bot/slack/commands`. The endpoint dispatches on the `command` field, so a single route handles all six.
3. Make sure the Slack `bot_connectors` row has both `bot_token` and `signing_secret` populated — the same signing secret powers the existing Events API, the interactions endpoint, and the slash command endpoint.
4. Add a `bot_user_links` row for every operator who should be able to click the buttons or run the slash commands (`POST /bot-connectors/{id}/user-links`). Slack users without a link get a friendly ephemeral "your account isn't linked" message and the action is ignored.

Slash commands accept an explicit incident UUID in the command text (e.g. `/ack 7f1c0e84-…`). When omitted, OpsMender resolves the user's most-recently-paged active incident automatically — so a paged operator can just type `/ack` to acknowledge the page they just received. `/snooze <duration>` accepts compact forms like `30m`, `2h`, `1d`; it pauses the chain and pushes `next_step_due_at` forward. `/status` with no arguments lists the org's active chains.

#### Per-incident Slack channels

If you want each paged incident to spawn its own workspace channel, turn on `slack_incident_channels_enabled` for the org (admin: `PUT /organizations/{id}/notification-settings`). When the chain starts in `page` mode, OpsMender calls `conversations.create` to make a deterministic `inc-<first8hex>` channel, stores the channel id on the incident, and posts the Block Kit page card there. The mirror is idempotent — re-running the kickoff is safe. The Slack app must have the `channels:manage` scope (or `groups:write` for private channels) plus the usual `chat:write`; missing scopes are logged and skipped without blocking the chain.

`OPSMENDER_SKILL_DEFINITION` should point to an operator-owned `SKILL.md` file. Different environments can use different skill files, for example:

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
opsmender config model list
opsmender config model list --provider ollama --base-url http://localhost:11434
```

You can persist the default model config into the database:

```bash
opsmender config model set --provider openai --model-id gpt-4o --api-key-env-var OPENAI_API_KEY
opsmender config model set --provider azure_openai --model-id my-deployment \
  --base-url https://example-resource.openai.azure.com/ \
  --api-version 2024-10-21 \
  --api-key-env-var AZURE_OPENAI_API_KEY
opsmender config model set --provider ollama --model-id llama3.2 --base-url http://localhost:11434
```

For first-run setup, OpsMender also ships a bootstrap path that prompts for missing fields:

```bash
opsmender config model bootstrap
opsmender config model bootstrap --provider openai --model-id gpt-4.1 --api-key-env-var OPENAI_API_KEY
```

Notes:

- `opsmender config model list` is discovery-only and does not write to the database.
- `opsmender config model set` and `opsmender config model bootstrap` store the config in `model_configs` and mark it as default.
- Provider-discovered model lists are suggestions, not a hard requirement. OpsMender allows explicit manual model IDs and returns warnings when discovery is stale, unavailable, or incomplete.
- Secrets are stored as **environment-variable references only**. The database stores values like `OPENAI_API_KEY`, not the raw provider secret itself.
- The dashboard supports the same first-run bootstrap flow from **Config → Models**, including provider, model ID, env-var reference, base URL, API version, max tokens, and temperature.
- If you want to run a local Hugging Face model with OpsMender, the clean path is to serve it through a local runtime such as Ollama or another OpenAI-compatible endpoint rather than loading raw checkpoints directly inside OpsMender.

## Workflow

OpsMender uses a LangGraph-powered incident response workflow:

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

Organizations define what's safe, cautious, or destructive in a `SKILL.md` file. This is one of the core design constraints of OpsMender: the framework enforces the skill definition you provide rather than deciding destructiveness itself.

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

This is how OpsMender supports operator-defined destructive actions: your `SKILL.md` files define the policy boundary for your environment, and OpsMender enforces that boundary programmatically.

### Skill Manager (UI + DB)

As of Sprint 12 Feature 3, skills are also managed from the dashboard:

- `/dashboard/skills` groups skills by MCP server with a "Global (unassigned)" section for the fallback skill.
- Admins can **Import** `.md` files, **Clone** a skill to a different MCP server, and create/edit/delete skills inline.
- Skills in `skills/` are auto-imported on backend startup — existing rows are skipped by name, so edits made in the UI are preserved across restarts.
- Enforcement looks up the skill bound to the session's MCP server first, then falls back to the global (unassigned) skill. If neither exists, behavior falls back to file-path loading via `OPSMENDER_SKILL_DEFINITION`.

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
4. A human approves or rejects via API or `opsmender approvals`.
5. If approved, OpsMender executes the action.
6. If rejected or expired, the action is blocked.

Default approval timeout is 15 minutes (`OPSMENDER_APPROVAL_TIMEOUT_SECONDS=900`).

## External Incident Ingestion

Sprints 14 + 15 added a webhook-based ingestion system that lets external monitoring/alerting tools create incidents in OpsMender automatically.

Sprint 15's **universal (`auto`) adapter** is now the default: a single endpoint accepts any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Prometheus Alertmanager, custom scripts — without requiring a per-platform adapter. Heuristics match common field names and envelopes first; unrecognized shapes fall back to an LLM that returns the field **paths** (cached per-token by a shape hash so the same payload shape pays the LLM cost only once).

### How It Works

1. An admin creates an **ingest token** via `POST /ingest-tokens`, specifying which provider adapter to use (default: `auto`).
2. The raw token (starts with `opsmender_ingest_...`) is returned **once** — save it. OpsMender stores only the SHA-256 hash.
3. External systems send JSON payloads to `POST /incidents/ingest` with the token in an `X-OpsMender-Token` header (or `Authorization: Bearer`).
4. OpsMender routes the payload through the chosen adapter (`auto` for universal, or a strict shape-specific adapter), which normalizes it into an incident.
5. For `auto` tokens: heuristic parse → LLM fallback on unrecognized shapes → per-token shape cache so the same payload shape skips the LLM next time. Admins can pre-train via `sample_payload` at creation, or `POST /ingest-tokens/{id}/learn-shape` later.
6. Dedup by `(external_source, external_id)` — repeated alerts update or skip instead of creating duplicates. `auto` tokens scope `external_source = "auto:<token-name>"` so cross-token ID collisions don't merge.
7. Every inbound payload is logged raw in the `ingest_log` table for replay/debugging.
8. Per-token rate limiting enforced (default: 60 req/min). Returns `429` with `Retry-After` header when exceeded.
9. Optional auto-start can create one session automatically for newly created incidents that match a configured source + minimum severity rule.

**Rate limit config** (in `.env`):
```dotenv
OPSMENDER_INGEST_RATE_LIMIT=60     # max requests per window per token (0 = disabled)
OPSMENDER_INGEST_RATE_WINDOW=60    # window size in seconds
```

**Optional ingest auto-start** (env defaults, also editable in `/dashboard/config`):
```dotenv
OPSMENDER_INGEST_AUTO_START_ENABLED=false
OPSMENDER_INGEST_AUTO_START_MIN_SEVERITY=critical
OPSMENDER_INGEST_AUTO_START_SOURCE=
```

When enabled, OpsMender auto-creates a single session only for newly created incidents whose `external_source` matches the configured source filter (or any source if blank) and whose severity is at or above the configured threshold. The new session inherits the current runtime tier, and duplicate ingests do not spawn extra active sessions.

### Supported Provider Adapters

| Provider | Key | Handles |
|----------|-----|---------|
| **Universal (auto-detect)** | `auto` | **Default.** Any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Alertmanager, custom scripts. Heuristics + LLM fallback with per-token shape cache. |
| CloudWatch | `cloudwatch` | SNS `SubscriptionConfirmation` + `Notification` envelopes with embedded alarm JSON |
| Azure Monitor | `azure_monitor` | Common alert schema v2 — maps severity (Sev0–4) and monitor condition |
| GCP Cloud Monitoring | `gcp_monitoring` | GCP incident webhook v1.2 — maps `state` (open/closed/acknowledged) |
| Oracle Cloud (OCI) | `oci_monitoring` | OCI alarm notifications — maps `status` (FIRING/OK/RESET) |
| LegacyAlertVendor | `legacy_alert_vendor` | v2 webhooks — `incident.triggered`, `.acknowledged`, `.resolved` |
| LegacyAlertRelay | `legacy_alert_relay` | Webhook integration payloads — `Create`, `Acknowledge`, `Close`, and update-style alert actions |
| Generic JSON | `generic` | Configurable dot-path field mapping — works with tools needing strict, deterministic parsing |

### Prometheus + Alertmanager

Alertmanager has a generic [`webhook_configs`](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config) receiver that works out of the box with the universal `auto` adapter — no code change, no typed adapter required.

1. Create an ingest token in OpsMender:
   ```bash
   curl -s http://localhost:8000/ingest-tokens \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"name":"alertmanager-prod","provider":"auto"}'
   # Save the returned `token` value — it's shown only once.
   ```

2. Add a receiver to `alertmanager.yml`:
   ```yaml
   route:
     receiver: opsmender
     group_by: ["alertname"]
     group_interval: 30s

   receivers:
     - name: opsmender
       webhook_configs:
         - url: "https://opsmender.example.com/incidents/ingest"
           http_config:
             authorization:
               type: Bearer
               credentials: "opsmender_ingest_..."   # the token from step 1
           send_resolved: true
   ```

3. Reload Alertmanager (`kill -HUP` or `curl -X POST .../-/reload`). The next firing alert will create an OpsMender incident; `send_resolved: true` lets OpsMender close the incident automatically when the underlying alert resolves.

The Alertmanager payload shape is well-known to the LLM, so the first webhook will train the per-token shape cache and subsequent payloads parse for free without an LLM call. The same pattern works for any monitoring tool that can POST JSON — Datadog (webhook actions), CloudWatch (SNS HTTPS subscription with `cloudwatch` adapter), Azure Monitor (action group webhook), Sumo Logic (webhook payload), Grafana (contact point: webhook), LegacyAlertVendor (webhook automations forwarding to OpsMender).

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
  -H "X-OpsMender-Token: $INGEST" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Disk Full","description":"98% on /data","severity":"high","id":"alert-001"}'
# → {"success":true,"dedup_action":"created",...}

# 4. Same payload again → dedup kicks in
curl -s http://localhost:8000/incidents/ingest \
  -H "X-OpsMender-Token: $INGEST" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Disk Full","description":"98% on /data","severity":"high","id":"alert-001"}'
# → {"success":true,"dedup_action":"skipped",...}
```

For full curl recipes covering all five providers (CloudWatch SNS, Azure Monitor, LegacyAlertVendor, LegacyAlertRelay, Generic), including lifecycle examples and severity mapping tables, see [`docs/REFERENCE.md`](docs/REFERENCE.md#external-incident-ingestion).

## Outbound Notifications

OpsMender also supports outbound collaboration notifications for session lifecycle events. This is separate from inbound alert ingestion:

- **Inbound**: external tools create incidents in OpsMender through `POST /incidents/ingest`
- **Outbound**: OpsMender notifies downstream systems when a session is created, awaits approval, becomes active, completes, fails, or times out

Outbound notifications are managed through saved **webhook triggers** in `/dashboard/config` or via the `/webhook-triggers` API. Each trigger subscribes to one or more session events and uses one of three payload formats:

| Format | Purpose | Payload |
|--------|---------|---------|
| `generic` | Any automation endpoint | OpsMender normalized JSON event |
| `slack` | Slack incoming webhook | `text` + Block Kit `blocks` |
| `teams` | Microsoft Teams Workflows webhook | plain `text` body |
| `sumo` | Sumo Logic HTTP source / JSON ingestion endpoint | log-friendly JSON event with flattened top-level fields plus nested session/incident objects |

Supported events:

- `session.created`
- `session.awaiting_approval`
- `session.active`
- `session.completed`
- `session.failed`
- `session.timed_out`
- `*` for all session events

Typical usage:

1. Create a trigger pointing at a Slack or Teams webhook URL.
2. Choose the delivery format (`slack` or `teams`).
3. Select which session events should notify the channel.
4. Use `POST /webhook-triggers/{id}/test` or the dashboard Test button to verify the destination.

The generic webhook system remains the underlying transport. Slack and Teams are just destination-specific renderers on top of the same trigger model.

## Custom Workflow Builder

OpsMender now supports a saved **workflow profile** builder on top of the fixed LangGraph node set:

- `observe`
- `diagnose`
- `plan`
- `tier_gate`
- `execute`
- `verify`
- `summarize`

This is not a free-form graph editor. Instead, admins create saved workflow profiles that choose the ordered subset of nodes a session should run. Sessions can use:

- the built-in default workflow when no profile is selected
- the default saved workflow profile
- an explicitly selected workflow profile at session start

Safety constraints stay enforced:

- `tier_gate` is still programmatic
- `execute` requires `tier_gate` immediately before it
- custom workflows cannot introduce arbitrary user-defined code or bypass tier enforcement

Workflow profiles are managed from `/dashboard/config` and via the `/workflow-profiles` API.

## Multi-Agent Teams

OpsMender also supports saved **agent team profiles** for multi-agent reasoning inside
the existing workflow. This is intentionally constrained:

- specialist roles are fixed to OpsMender's built-in set:
  - `incident_commander`
  - `investigator`
  - `skeptic`
  - `remediator`
- selected roles each produce their own reasoning pass for `observe`,
  `diagnose`, `plan`, `verify`, and `summarize`
- OpsMender then synthesizes those role outputs into a single final answer
- `tier_gate` and `execute` remain single-path and programmatic

Sessions can use:

- the default single-agent path when no team is selected
- the default saved agent team profile
- an explicitly selected agent team profile at session start

Agent team profiles are managed from `/dashboard/config` and via the
`/agent-team-profiles` API.

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
│   │   ├── adapters/       # Provider adapters (cloudwatch, azure_monitor, gcp_monitoring, oci_monitoring, legacy_alert_vendor, legacy_alert_relay, generic)
│   │   ├── registry.py     # Adapter registry (provider key → adapter class)
│   │   └── service.py      # Token auth, adapter dispatch, dedup, audit logging, availability signal → uptime_samples
│   ├── approvals/          # Tier 1 approval service and wait/timeout logic
│   ├── audit/              # JSONL audit logger + PgAuditLogger + audited executor
│   ├── config_loader.py    # .env/AppConfig loader + typed dataclasses
│   ├── db/                 # SQLAlchemy models, async repos, Alembic migrations
│   ├── llm/                # Provider abstraction, registry, and factories
│   ├── mcp/                # MCP client wrapper (stdio/sse/http) + dynamic server pool
│   ├── skills/             # Skill definition parser (SKILL.md) + startup auto-importer
│   ├── webhooks/           # Outbound webhook trigger rendering + delivery
│   ├── api/routes/bot_*    # Chat connector management + inbound bot webhooks
│   └── tiers/              # Tier enforcement layer
├── cli/
│   └── opsmender.py              # CLI entry point (run, check, audit, config, approvals, migration helpers)
├── examples/
│   └── SKILL.md            # Reference Kubernetes skill definition
├── skills/                 # Operator-owned environment skill files (auto-imported on startup)
├── tests/                  # full unit + integration suite, plus tests/test_e2e.py + tests/test_frontend_mount.py for the single-container chain
├── .env                    # Deployment defaults / secrets / local fallbacks
└── docs/                   # Project documentation
```

## Progress

- **Phase 1 (Sprints 1–6):** ✅ Complete — CLI, MCP, skills, tiers, audit, LangGraph workflow
- **Phase 2 (Sprints 7–16):** ✅ Complete — persistence, REST/WebSocket API, approvals, BYOM, frontend, single-container distribution, external ingestion
- **Phase 3 (Sprints 17–23):** ✅ Complete — Tier 0 sandbox + rollback, outbound webhook triggers (Slack/Teams/Sumo), custom workflow profiles, multi-agent team profiles
- **Sprint 24:** ✅ Complete — UI polish + public release
- **Sprint 25:** ✅ Complete — SLA / SLO dashboard + maintenance windows, downsampling, availability ingest, and SLO incident wiring
- **Sprint 26:** ✅ Complete — repo-hosted user documentation wiki and operator/admin guides
- **Sprint 27:** ✅ Complete — chat bot integrations: 15 platforms (Slack, Discord, MS Teams, etc.), persisted connector configs, Config/Integrations management UI, and full incident/session/approval webhook commands.
- **Sprint 28:** ✅ Complete — multi-tenant transition: organization model, tenant data isolation, automated registration flow, and per-org custom branding (logo/colors).

### Sprint breakdown
  - Sprint 7: ✅ Database layer (SQLAlchemy + Alembic + async repos)
  - Sprint 8: ✅ FastAPI REST + WebSocket layer (JWT auth, RBAC, all CRUD endpoints)
  - Sprint 9: ✅ Tier 1 approval flow
  - Sprint 10: ✅ BYOM provider abstraction
  - Sprint 11: ✅ Next.js frontend + Docker setup
  - Sprint 12: ✅ Config consolidation + UI self-service (foundation, model manager, dynamic MCP pool, `/dashboard/config` MCP manager, Skill Manager `/dashboard/skills`, Co-pilot Chat)
  - Sprint 13: ✅ Single-container app — `opsmender serve` + unified `docker/Dockerfile` + PyInstaller binary, E2E + frontend-mount verification green
  - Sprint 14: ✅ External incident ingestion — core API + 5 provider adapters + dedup + ingest audit log + admin UI + curl recipes + rate limiting + auto-start
  - Sprint 15: ✅ Universal ingestion — `auto` adapter with heuristics + LLM fallback + per-token shape cache
  - Sprint 16: ✅ Bundle Node.js/npx in Docker + binary builds
  - Sprint 17: ✅ Tier 0 sandbox + hard time limits + rollback
  - Sprint 18: ✅ Outbound webhook triggers — persisted configs, async session event delivery, CRUD/test API
  - Sprint 19: ✅ Outbound webhook trigger UI — dashboard management + safe edit semantics for headers/tokens
  - Sprint 20: ✅ Slack + Teams outbound trigger formats on top of the generic webhook trigger system
  - Sprint 21: ✅ Sumo Logic outbound trigger format on top of the generic webhook trigger system
  - Sprint 22: ✅ Custom workflow builder — saved workflow profiles wired into sessions, API, graph builder, and config UI
  - Sprint 23: ✅ Multi-agent support — saved agent team profiles wired into sessions, API, multi-agent node synthesis, and config UI
  - Sprint 24: ✅ UI polish + public release prep
  - Sprint 25: ✅ Reliability dashboard + SLA/SLO APIs + maintenance windows
  - Sprint 26: ✅ User documentation wiki + operator guides
  - Sprint 27: ✅ Chat bot integrations (15 platforms, interactive commands, identity mapping)
  - Sprint 28: ✅ Multi-tenant transition (data isolation, custom branding, automated registration)

## Distribution Status

The current target is a standalone binary plus operator-owned config/assets:

```
opsmender                  # standalone binary
.env                 # deployment defaults, API keys, database URL, JWT secret
skills/              # operator-owned skill definitions for each environment
```

The repo ships the PyInstaller spec/build script and the unified `opsmender serve` entrypoint; the full chain (auth → incident → session → approval → execute → audit, plus the static frontend mount) is covered by `tests/test_e2e.py` and `tests/test_frontend_mount.py`.

See `docs/REFERENCE.md` for full architecture details.
