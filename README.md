# OpsMender AI

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/SpicyDaemon/OpsMender-AI?include_prereleases&sort=semver)](https://github.com/SpicyDaemon/OpsMender-AI/releases)

> **OpsMender AI** — open-source **AI incident manager** / **AI SRE** / **AI on-call** for production infrastructure. Tier-gated, MCP-first, human-in-the-loop AI incident response and incident management.

📚 **[Documentation Wiki](docs/wiki/README.md)** · 🛠 **[Architecture & API Reference](docs/REFERENCE.md)** · 🤝 **[Contributing](CONTRIBUTING.md)**

*Keywords: AI incident manager, AI incident management, AI incident response, AI SRE, AI on-call, agentic incident response, LangGraph incident response, MCP runbook automation.*

---

## What is OpsMender

OpsMender is a self-hosted AI incident-response framework. It connects AI agents to your infrastructure via **Model Context Protocol (MCP) servers** that you provide, then enforces a **three-tier AI autonomy model** (Autonomous / Approval Required / Advisory Only) so the agent can only do what you allow. Operators classify each tool as `safe`, `caution`, or `destructive` in an MCP Skill; the tier gate is enforced programmatically — the agent cannot reason its way past it.

A single team installs OpsMender, invites their on-call operators, connects a model, connects MCP servers, defines what's safe vs destructive, wires up ingest from their monitoring stack, and from then on every paged incident walks the same loop: **alert → AI → ack → fix → resolve** — with a full audit trail and an authored postmortem at the end.

## Why OpsMender

> **Simple by default. Enterprise-ready underneath.** Spin OpsMender up as a single-workspace self-hosted tool with email + admin invites — multi-tenant, SSO, SAML, and host-based domain isolation stay in the codebase and turn on when you need them, not before.

- **MCP-first** — every infrastructure action goes through an MCP server the operator provides. No native integrations locked to one cloud or tool.
- **Tiered AI autonomy** — three tiers: Tier 0 Autonomous, Tier 1 Approval Required, Tier 2 Advisory Only (the default). Tier 0 has a sandbox, hard time limits, and automatic rollback.
- **Human in the loop** — Tier 1 pauses the workflow on destructive actions and requires explicit approval from an operator or admin.
- **Programmatic tier gate** — enforced in code, not by prompt. The agent cannot reason its way past it.
- **Org-owned skill definitions** — a single `SKILL.md` classifies every operation as `safe`, `caution`, or `destructive`. Your call, not ours.
- **AI incident memory** — successful sessions leave behind short markdown lessons; the next similar incident gets them injected into the agent's prompt before the first observe call. Per-org, advisory only (never bypasses tier or skill gates), bounded by auto-compaction, and rankable by operator thumbs up/down via `/dashboard/memories`.
- **Full audit log** — every node transition, every tool call, every approval, every rollback step. Memory recall and writeback are audited too.
- **Bounded storage** — logs auto-prune after 90 days by default (operator-overridable from Config → "Storage & retention"); memories are operator-curated and never auto-deleted.
- **Bring your own model** — Anthropic, OpenAI, Azure OpenAI, AWS Bedrock, GCP Vertex AI, Ollama, and any OpenAI-compatible endpoint.
- **Universal ingest** — accept webhooks from CloudWatch, Azure Monitor, GCP Cloud Monitoring, Oracle Cloud (OCI), Grafana, Datadog, Slack, Prometheus Alertmanager, or anything else that POSTs JSON.
- **Outbound triggers** — fire session-lifecycle notifications to Slack, Teams, Sumo Logic, or any generic webhook endpoint.
- **Command Palette** — `Cmd+K` / `Ctrl+K` opens a type-to-filter palette from anywhere in the dashboard.
- **Advanced — Multi-tenant (opt-in).** Strict per-org isolation across every entity, fully tested. Hidden in single-workspace mode (the default); enable with `OPSMENDER_MULTI_ORG_ENABLED=true`. Optional host-based routing pins each tenant to its own URL.
- **Advanced — Per-tenant SSO / SAML (opt-in).** Each org can wire its own **OIDC** identity provider (Okta, Azure AD, Google Workspace, Auth0, Keycloak) **or SAML 2.0** IdP. Email + password remains available as a break-glass path.

The auth model splits along the simple-by-default axis: see [docs/wiki/auth-guide.md](docs/wiki/auth-guide.md) for the default flow (single workspace, email + admin invite, three roles), [docs/wiki/people-guide.md](docs/wiki/people-guide.md) for day-to-day People-page operations, and [docs/wiki/advanced-auth-guide.md](docs/wiki/advanced-auth-guide.md) for the opt-in surfaces.

---

## How OpsMender works

### The full incident-response loop

Every paged incident walks the same five stages.

```
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                │
   │  1. ALERT FIRES                                                │
   │     Prometheus / Datadog / CloudWatch / Azure Monitor /        │
   │     Cloud / Slack alerts / anything-that-POSTs-JSON            │
   │     hits /incidents/ingest with a service-scoped token.        │
   │                                                                │
   │  2. AI STARTS WORKING                                          │
   │     Priority rule decides P0/P1/P2/P3 + response mode          │
   │     (auto_resolve / notify / page / escalate_immediate).       │
   │     LangGraph workflow runs the tier-gated session in          │
   │     parallel — Tier 0 fixes autonomously, Tier 1 pauses on     │
   │     destructive actions for approval, Tier 2 (default) is      │
   │     advisory only.                                             │
   │                                                                │
   │  3. OPERATOR ACKS                                              │
   │     Page mode → escalation chain fires step 0; on-call user    │
   │     gets a Slack DM / Teams DM / Email / SMS with              │
   │     Acknowledge / Resolve / Escalate / Start Session buttons.  │
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
   │     preserved for postmortem. Author the postmortem from the   │
   │     Incident Command Strip — dedicated editor with the seven   │
   │     recommended sections + memory-candidates handoff into AI   │
   │     incident memory.                                           │
   │                                                                │
   └────────────────────────────────────────────────────────────────┘
```

For the operator-facing walkthrough — services / teams / escalation chains / rosters / maintenance windows / notifications — see [docs/wiki/paging-guide.md](docs/wiki/paging-guide.md). For platform-specific chat-surface details, see the [Slack](docs/wiki/slack-paging-surface.md) and [Teams](docs/wiki/teams-paging-surface.md) guides.

For admin-facing user lifecycle work, see [docs/wiki/people-guide.md](docs/wiki/people-guide.md).

### Core concepts

Four configurable surfaces drive the behavior under the loop above:

**Alert Intake — getting alerts in (stage 1).** Your monitoring tools (Prometheus Alertmanager, Datadog, CloudWatch, Azure Monitor, Sumo Logic, Grafana, anything that POSTs JSON) send alerts into OpsMender; OpsMender creates an incident from the payload and runs the tier-gated AI response workflow. In v1, Services are the alert-intake surface: each service exposes `POST /api/v1/intake/{service_token}`, where the embedded unguessable token lets external monitors POST directly without separate API-key headers. The older `/incidents/ingest` token backend remains for compatibility.

**Paging — who gets pinged (stage 3).** OpsMender owns paging end-to-end inside the product. Configure **teams**, **escalation chains**, **services**, **rosters** (with deterministic coverage windows in IANA time zones), **maintenance windows**, and **notifications** under the **Paging & On-call** sidebar group. Each service owns its fixed priority (`P0`–`P3`), ordered **Preferred MCP servers**, and up to three ranked **Preferred Models**. Preferred MCPs guide the AI toward likely tools first; they are not a hard allowlist. Preferred models are tried in order, then OpsMender falls back to another enabled model. **Maintenance windows** drop matching alerts during planned work so they do not create visible incidents.

**Workflows — the order of the autonomous response steps (stage 2).** When a session runs, OpsMender walks a LangGraph: `observe → diagnose → plan → tier_gate → execute → verify → summarize`. A **Workflow profile** lets you save a different node order — same nodes, just rearranged or trimmed. The tier gate must always sit immediately before `execute` (programmatic safety floor; cannot be moved or removed). Most operators never touch this.

**Agent teams — which specialist personas reason about the problem.** Inside the reasoning nodes (`diagnose`, `plan`), instead of one generic LLM pass, you can configure multiple specialist roles to each take a pass — `incident_commander`, `investigator`, `skeptic`, `remediator` — followed by a synthesis pass. Saved as **Agent team profiles**. Default is one generic persona.

**Viewer Updates — getting events out.** Whenever a session changes state (`created`, `awaiting_approval`, `active`, `completed`, `failed`, `timed_out`), OpsMender can POST viewer-facing updates to configured URLs. These live inside **Paging & On-call → Notifications**, separate from operator paging. Format presets exist for **Slack** incoming webhooks, **Teams** workflow webhooks, **Sumo Logic**, or **generic JSON**.

**AI incident memory — carrying lessons forward.** Each successfully resolved session writes one short markdown lesson into the per-org `incident_memories` table, scoped to the service that owned the incident. On the next incident for that service, a `recall` node runs *before* `observe` — pure SQL match on service + tag overlap + keyword match, weighted by operator thumbs up/down. The top 5 matches get injected into the agent's system prompt as a `### Past lessons from similar incidents` block. Memory is per-org isolated, advisory only (cannot bypass tier gates), written via a strict JSON-schema-validated post-session `remember` node (no prompt-injection path from chat or tool output), skipped on failed sessions, bounded by auto-compaction at 50 memories per service, and operator-curated via `/dashboard/memories`. Postmortem authors curate the next batch of memories from the per-incident editor — see [docs/wiki/postmortem-guide.md](docs/wiki/postmortem-guide.md).

### Where each concept lives in the dashboard

| Sidebar group | Frequency | What's in it |
|---|---|---|
| **Incident Management** | Always | Dashboard, Incidents, Approvals |
| **Paging & On-call** | Most operators | Teams, Escalation Chains, Services, Rosters, Maintenance Windows, Notifications |
| **AI Agent** (Day-1 setup) | Always | Skills, Memories, MCP Servers, Models |
| **Observe** | Operators | Reliability, Activity |
| **Admin** | Admins | People, Workspace Settings, Config |

Session Profiles (the saved AI-session node order, formerly "Workflows") live under **Config → Advanced** as advanced configuration; most users keep the default. Agent Teams (multi-agent reasoning) is deferred from the v1 dashboard — see [`docs/ROADMAP.md`](docs/ROADMAP.md) for what v1.0.0 includes vs. what's planned for v1.1 / v1.2 / v2.0.

If you're new to OpsMender, work top-down: get one model + one MCP server + one skill definition working (`/dashboard/models`, `/dashboard/mcp-servers`, `/dashboard/skills`), then create a service under **Paging & On-call** and treat that service as the home for alert intake, priority, preferred MCP servers, preferred models, and escalation setup. Manual incidents must be linked to an active service.

---

## Run in development mode

Goal: get OpsMender running on your laptop with zero secret rotation and a default admin login, in three commands.

**Requires:** Docker (with Compose v2).

```bash
git clone https://github.com/SpicyDaemon/OpsMender-AI.git
cd OpsMender-AI
cp .env.example .env
docker compose -f docker/docker-compose.yml up --build
```

Open **http://localhost:8000**.

`.env.example` ships with `OPSMENDER_DEPLOYMENT_MODE=development`, which tells the API to accept the placeholder JWT secret as-is. The bundled Postgres container persists data in a named Docker volume; the app waits for `db` to be healthy, runs Alembic migrations, then binds Uvicorn to port 8000.

> **First-time login:** Self-signup is closed. In **development mode** (the `.env.example` default), a fresh database is seeded with a default admin — sign in with `admin` / `admin123`. To use your own first admin instead (required for production), set `OPSMENDER_BOOTSTRAP_ADMIN_EMAIL` + `OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD` in `.env` before bringing the stack up — those become the first admin and the `admin`/`admin123` default is **not** created. Production mode never seeds a default admin.

To stop and remove containers:

```bash
docker compose -f docker/docker-compose.yml down
```

To also wipe the Postgres volume (full reset):

```bash
docker compose -f docker/docker-compose.yml down -v
```

## Run in production mode

Same `docker-compose.yml` — the difference is **a stricter `.env`**. The API refuses to start if any of the required production values are missing or still on the placeholder.

```bash
cp .env.example .env
```

Then edit `.env` and set, at minimum:

```dotenv
# 1. Switch the deployment-mode guard from "accept defaults" to "reject defaults"
OPSMENDER_DEPLOYMENT_MODE=production

# 2. Strong JWT secret — generate with: openssl rand -hex 32
OPSMENDER_JWT_SECRET=<paste 32+ random bytes here>

# 3. First admin — without these, no one can log in on a fresh DB
OPSMENDER_BOOTSTRAP_ADMIN_EMAIL=you@example.com
OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD=<strong password>
```

**Browser session duration:** a successful login keeps you signed in for **7 days**
by default (`OPSMENDER_JWT_EXPIRE_MINUTES=10080`, i.e. 604800 seconds) before a
re-login is required. Reloading or reopening the browser keeps the session; logout
clears it immediately, and deactivated/deleted users are rejected even within the
window. MFA is deferred to v2.

Then bring the stack up:

```bash
docker compose -f docker/docker-compose.yml up --build -d
```

The app is reachable on port 8000. Put a TLS-terminating reverse proxy (nginx, Caddy, Cloudflare, your cloud's LB) in front of it for any deployment exposed beyond `localhost`.

### Required environment variables

| Variable | Required when | Purpose |
|---|---|---|
| `OPSMENDER_DEPLOYMENT_MODE` | Always | `development` (accept placeholder secrets) or `production` (reject them). Defaults to `development` in `.env.example`. |
| `OPSMENDER_JWT_SECRET` | Production | Signs auth tokens. The default placeholder is rejected in production mode. Generate with `openssl rand -hex 32`. |
| `OPSMENDER_BOOTSTRAP_ADMIN_EMAIL` | Production (first run) | Email for the first admin user, created automatically on a fresh database. |
| `OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD` | Production (first run) | Password for the first admin user. |
| `OPSMENDER_DATABASE_URL` | Auto-wired by compose | Async SQLAlchemy URL. Compose builds this from the bundled Postgres container; override only if you're pointing at an external DB. |
| `OPSMENDER_PUBLIC_BASE_URL` | Production (recommended) | Absolute URL the dashboard is reachable at. Used to build invite + password-reset links. |
| Provider API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) | Per-model | Only the providers you actually configure under Admin → Models need keys. |

All other configuration (tier, log level, ingest rate limits, SMTP, SAML SP keypair, multi-org flag, advanced-auth flag, retention windows) is documented inline in `.env.example`.

### After first login (production checklist)

1. **Models** — open `/dashboard/models`, click **Add model**, pick a provider, fill in fields (`Refresh catalog` will list available IDs once credentials resolve). Set one as default.
2. **MCP servers** — open `/dashboard/mcp-servers`, click **Add server**, point at a stdio/SSE/HTTP MCP endpoint. Use **Test** to confirm connectivity. The Config → MCP Servers modal ships curated templates for common server shapes (Kubernetes, Postgres, GitHub Copilot MCP, generic HTTP/bearer/stdio).
3. **Skills** — open `/dashboard/skills`, click **Import** to upload a `SKILL.md` (start from [`examples/SKILL.md`](examples/SKILL.md) for infra ops or [`examples/SKILL.app-incident.md`](examples/SKILL.app-incident.md) for app-layer incident response). The file classifies each MCP tool as `safe` / `caution` / `destructive` — the tier gate enforces these at runtime. Skills dropped into the local `skills/` directory are auto-imported on backend startup.
4. **Services / Alert Intake** — open `/dashboard/paging/services`, create a service and a team, then use that service as the home for inbound alerts. Each service exposes its own intake webhook URL with an embedded unguessable secret. (The legacy ingest-token API remains for internal compatibility, but the standalone Ingest Tokens page was removed from the v1 UI — think in terms of the service intake URL.)
5. **Paging** — attach a roster (on-call rotation), then attach a priority rule + escalation chain so paged incidents actually notify someone. Walkthrough: [docs/wiki/paging-guide.md](docs/wiki/paging-guide.md).
6. **Notification channels** — configure workspace-level channels at `/dashboard/paging/notification-channels`, then have each operator set their personal routing preferences at `/dashboard/paging/my-notifications`.
7. **People** — invite the rest of the team from `/dashboard/people` (or create users directly). Three roles: admin / operator / viewer. The first admin comes from the bootstrap step above (dev default or `OPSMENDER_BOOTSTRAP_ADMIN_*`), not self-signup — self-registration is closed.
8. **(Optional) Tier** — `/dashboard/config` sets the runtime tier. Default `2` (safe operations only). Move to `1` (approval-gated execution) once your operators are confident with the audit trail.

### Health check

The app exposes an unauthenticated `GET /health` endpoint that returns `{"status":"ok"}` once Uvicorn is bound and the DB is reachable. The Docker Compose healthcheck polls it every 30s. From the host:

```bash
curl -fsS http://localhost:8000/health
# {"status":"ok"}
```

Containers `db` (Postgres) and `app` (OpsMender) both report `(healthy)` in `docker compose ps` once steady state is reached.

### Troubleshooting

- **`OPSMENDER_JWT_SECRET is still the default placeholder` at startup.** You're in production mode with the placeholder still in `.env`. Generate a strong value: `openssl rand -hex 32`, set it on `OPSMENDER_JWT_SECRET=`, and restart.
- **Login page accepts no credentials on a fresh production install.** No first admin was bootstrapped. Set `OPSMENDER_BOOTSTRAP_ADMIN_EMAIL` + `OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD` in `.env`, run `docker compose down`, then `docker compose up -d --build`.
- **`/health` returns 500 / Uvicorn never binds.** Check `docker compose logs app`. Most common: `OPSMENDER_DATABASE_URL` points at an unreachable host, or Alembic migration failed (look for the SQLAlchemy traceback above the Uvicorn startup line).
- **Port 8000 already in use on the host.** Either stop the conflicting process or change the host-side mapping in `docker-compose.yml` (`"8001:8000"` to bind 8001 on the host while keeping the container internal port).
- **`/dashboard/models` says "no providers reachable".** Run a one-shot probe: `docker compose exec app opsmender config model list`. The CLI shows which providers respond and which credentials are missing.

### Optional features

Each of these is configured by adding env vars to `.env` and restarting — nothing is required to get the core loop working.

| Feature | Env vars (see `.env.example` for full inline docs) |
|---|---|
| **Slack DM paging** | `OPSMENDER_SLACK_BOT_TOKEN` (+ a `bot_connectors` row with `signing_secret`) |
| **Teams legacy webhook paging** | `OPSMENDER_TEAMS_WEBHOOK_URL` |
| **Teams Graph paging** | `OPSMENDER_TEAMS_GRAPH_TENANT_ID`, `OPSMENDER_TEAMS_GRAPH_CLIENT_ID`, `OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET` |
| **Email paging** | `OPSMENDER_SMTP_HOST`, `OPSMENDER_SMTP_PORT`, `OPSMENDER_SMTP_USER`, `OPSMENDER_SMTP_PASSWORD`, `OPSMENDER_SMTP_FROM`, `OPSMENDER_SMTP_USE_TLS` |
| **SMS paging** | `OPSMENDER_TWILIO_ACCOUNT_SID`, `OPSMENDER_TWILIO_AUTH_TOKEN`, `OPSMENDER_TWILIO_FROM_NUMBER` |
| **Slack interactivity (Ack/Resolve/Escalate/Start AI Session + slash commands)** | Add Slack app **Request URL**s for `/bot/slack/interactions` and `/bot/slack/commands`; populate `bot_token` + `signing_secret`, then enable verified Slack actions on the channel |
| **Teams interactivity (Ack/Resolve/Escalate/Start AI Session)** | Configure Graph app credentials + Bot Framework app ID, set the messaging endpoint to `/bot/teams/activity`, link Azure AD object IDs, then enable verified Teams actions |
| **Per-incident Slack channels** | Toggle `slack_incident_channels_enabled` per org (`PUT /organizations/{id}/notification-settings`); requires Slack app `channels:manage` + `chat:write` scopes |
| **Ingest auto-start** | `OPSMENDER_INGEST_AUTO_START_ENABLED=true`, `OPSMENDER_INGEST_AUTO_START_MIN_SEVERITY=critical`, optional `OPSMENDER_INGEST_AUTO_START_SOURCE` filter; only resolved Tier 0 sessions auto-start |
| **SLA poller (HTTP / TCP uptime checks)** | `OPSMENDER_SLA_POLLER_ENABLED=true`, `OPSMENDER_SLA_POLL_INTERVAL_DEFAULT=60` |
| **Multi-tenant orgs** | `OPSMENDER_MULTI_ORG_ENABLED=true` (exposes TopBar org switcher + per-invite org picker) |
| **Per-tenant OIDC / SAML admin UI** | `OPSMENDER_ADVANCED_AUTH_ENABLED=true` (runtime routes work regardless; this just surfaces the admin pages) |
| **SAML SP keypair** | `OPSMENDER_SAML_SP_CERT` + `OPSMENDER_SAML_SP_KEY` (generate with `opsmender saml gen-sp-keys`) |
| **Bot connector OAuth installs** | `OPSMENDER_SLACK_OAUTH_CLIENT_ID/SECRET`, `OPSMENDER_DISCORD_OAUTH_CLIENT_ID/SECRET` |

Channels with missing credentials are silently skipped — the dispatcher writes an `incident_pages` row with `delivery_status='skipped'` and reason `channel_unconfigured` so operators can see in the audit log which channel was unavailable.

---

## Other deployment paths

Docker Compose is the recommended on-ramp. Three other paths are first-class:

### Kubernetes (Helm)

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm dependency build ./deploy/helm/opsmender

# Helm 4 — extract the subchart archive manually (skip on Helm 3)
( cd ./deploy/helm/opsmender/charts && tar -xzf postgresql-*.tgz )

helm install opsmender ./deploy/helm/opsmender \
  --namespace opsmender --create-namespace
```

The chart auto-generates `OPSMENDER_JWT_SECRET` on first install and preserves it across `helm upgrade` — no `openssl rand` step. Bring your own Postgres via `externalDatabase.url`, override the image tag, or add an Ingress + TLS as documented in [deploy/helm/opsmender/README.md](deploy/helm/opsmender/README.md).

### Standalone binary

PyInstaller executables are attached to every `v*` release for Linux x64 and Windows x64 (with matching `.sha256` checksums):

- `opsmender-v1.0.0-linux-x64.tar.gz`
- `opsmender-v1.0.0-windows-x64.zip`

Extract, then run against an external Postgres:

```bash
OPSMENDER_DATABASE_URL=postgresql+asyncpg://user:pw@db.internal/opsmender \
OPSMENDER_DEPLOYMENT_MODE=production \
OPSMENDER_JWT_SECRET=$(openssl rand -hex 32) \
OPSMENDER_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD='<strong password>' \
./opsmender serve
```

> **Node.js for the binary:** The PyInstaller binary does **not** bundle Node.js. If your MCP servers run via `npx` (e.g. `@anthropic/mcp-server-k8s`), install Node.js LTS on the host and make sure `npx` is on `$PATH`, or set `OPSMENDER_NODE_PATH=/path/to/node/bin`.

### Cloud recipes

Reference IaC for the four major clouds, all pulling the same container image:

- AWS ECS Fargate (Terraform) — [deploy/cloud/aws-ecs/](deploy/cloud/aws-ecs/)
- Azure Container Apps (Bicep) — [deploy/cloud/azure-containerapps/](deploy/cloud/azure-containerapps/)
- GCP Cloud Run (service YAML) — [deploy/cloud/gcp-cloud-run/](deploy/cloud/gcp-cloud-run/)
- OCI Container Instances (Terraform) — [deploy/cloud/oci-container-instances/](deploy/cloud/oci-container-instances/)

Each recipe wires secrets through the cloud's native secret manager (Secrets Manager, Key Vault, Secret Manager, OCI Vault) and expects operator-managed Postgres (RDS / Cloud SQL / Azure Database for PostgreSQL / etc.).

---

## AI Autonomy Tiers

The **AI Autonomy Tier** controls how much the AI agent may do during an incident
session. It is **separate** from incident priority (P0–P3) and user role
(Admin/Operator/Viewer). The default is **Tier 2 — Advisory Only**.

| Tier | Mode | What the AI may do |
|------|------|--------------------|
| **0** | **Autonomous** | May execute remediation automatically — including rollbacks, restarts, failovers, and destructive ops — **but only within MCP Skill policy, deny lists, MCP permissions, and backend guardrails.** Most autonomous, not unlimited. |
| **1** | **Approval Required** | May investigate and propose actions; safe/allow-listed actions run, destructive/high-risk actions pause for operator approval, deny-listed actions never run. |
| **2** | **Advisory Only** *(default)* | Analysis, recommendations, runbooks, and read-only observation only. **No write/remediation actions execute.** |

> **Skills guide the AI. The backend tier gate enforces what can actually run.**
> The tier gate is a hard programmatic check in `backend/tiers/enforcement.py` —
> the agent cannot reason its way past it. Unknown actions are never silently
> allowed (denied at every tier). See [MCP Skills](docs/wiki/mcp-skills.md).

Operators may override the default tier when starting a session; selecting Tier 0
shows a strong red warning. The selected tier is recorded on the session and in
the audit/activity log. (Legacy installs that stored a fourth "Tier 3 — advise-only"
value are automatically remapped to Tier 2.)

### Tier 1 approval flow

1. The workflow reaches `tier_gate`.
2. A destructive action at Tier 1 creates an `approval_request`.
3. The session moves to `awaiting_approval`.
4. A human approves or rejects via API, dashboard, Slack DM action, or `opsmender approvals`.
5. If approved, OpsMender executes the action.
6. If rejected or expired, the action is blocked.

Default approval timeout is 15 minutes (`OPSMENDER_APPROVAL_TIMEOUT_SECONDS=900`).

## Skill definitions

Organizations define what's safe, cautious, or destructive in a `SKILL.md` file. This is one of the core design constraints of OpsMender: the framework enforces the skill definition you provide rather than deciding destructiveness itself.

That means users can bring their own skills to match their environment. For example, deleting a pod in production might be classified as `destructive`, while the same action in a sandbox environment might be treated differently.

See [`examples/SKILL.md`](examples/SKILL.md) for a Kubernetes (infra-layer) reference template, and [`examples/SKILL.app-incident.md`](examples/SKILL.app-incident.md) for an application-layer template that wires up GitHub/GitLab + Jira + observability MCPs so the agent can diagnose app bugs, file Jira tickets, and propose code fixes as pull requests / merge requests (humans still review and merge).

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

### Skill manager (UI + DB)

Skills are managed from the dashboard:

- `/dashboard/skills` groups skills by MCP server with a "Global (unassigned)" section for the fallback skill.
- Admins can **Import** `.md` files, **Clone** a skill to a different MCP server, start **New from Template**, and create/edit/delete skills inline.
- **Generate from MCP** (Skill Studio) discovers a saved MCP server's live tools and suggests a starting classification for each (generic command tools are flagged and suggested deny), then builds an editable 3-tier skill draft from your reviewed classifications. An optional **AI assist** (when a model is configured) uses a freeform intent prompt to suggest classifications and author per-tier guidance — but the operator reviews every row, generic command tools stay force-denied, and the backend tier gate remains the execution authority. See [docs/wiki/mcp-skills.md](docs/wiki/mcp-skills.md).
- Skills in `skills/` are auto-imported on backend startup — existing rows are skipped by name, so edits made in the UI are preserved across restarts.
- Enforcement looks up the skill bound to the session's MCP server first, then falls back to the global (unassigned) skill. If neither exists, behavior falls back to file-path loading via `OPSMENDER_SKILL_DEFINITION`.

---

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

### Custom workflow profiles

Saved workflow profiles choose the ordered subset of nodes a session should run. Sessions can use:

- the built-in default workflow when no profile is selected
- the default saved workflow profile
- an explicitly selected workflow profile at session start

Safety constraints stay enforced: `tier_gate` is still programmatic, `execute` requires `tier_gate` immediately before it, and custom workflows cannot introduce arbitrary user-defined code or bypass tier enforcement. Managed from `/dashboard/workflows` and via the `/workflow-profiles` API.

### Multi-agent teams

Saved **agent team profiles** add multi-agent reasoning inside the existing workflow with a fixed specialist role set: `incident_commander`, `investigator`, `skeptic`, `remediator`. Selected roles each produce their own reasoning pass for `observe`, `diagnose`, `plan`, `verify`, and `summarize`; OpsMender then synthesizes those role outputs into a single final answer. `tier_gate` and `execute` remain single-path and programmatic. Managed from `/dashboard/agent-teams` and via the `/agent-team-profiles` API.

## Model providers

| Provider | Notes |
|---|---|
| Anthropic | `OPSMENDER_MODEL_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` |
| OpenAI | `OPSMENDER_MODEL_PROVIDER=openai`, `OPENAI_API_KEY` |
| Azure OpenAI | `OPSMENDER_MODEL_PROVIDER=azure_openai`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` |
| AWS Bedrock | Native AWS credential chain (env vars → shared credentials → IAM role); UI collects region + optional profile |
| GCP Vertex AI | Application Default Credentials; UI collects project + location; discovery uses `publisher/model` IDs (`google/gemini-2.5-flash`, `anthropic/claude-sonnet-4@20250514`) |
| Ollama | `OPSMENDER_MODEL_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://host.docker.internal:11434` |
| OpenAI-compatible | `OPSMENDER_MODEL_PROVIDER=openai_compatible`; requires `base_url`, optional `api_key_env_var` |

Notes:

- Secrets are stored as **environment-variable references only**. The database stores values like `OPENAI_API_KEY`, not the raw provider secret itself.
- Provider-discovered model lists are suggestions, not a hard requirement. OpsMender allows explicit manual model IDs and returns warnings when discovery is stale or unavailable.
- The dashboard's **Config → Models** page supports first-run bootstrap, model discovery via **Refresh Catalog**, and per-provider routing fields (Bedrock region/profile, Vertex project/location, Azure deployment + API version).

Inspect provider availability from the CLI:

```bash
docker compose exec app opsmender config model list
docker compose exec app opsmender config model bootstrap
```

## External incident ingestion

OpsMender's universal adapter accepts any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Prometheus Alertmanager, custom scripts — without requiring a per-platform adapter. Heuristics match common field names first; unrecognized shapes fall back to an LLM that returns the field **paths** (cached per-token by a shape hash so the same payload shape pays the LLM cost only once).

### How it works

1. An admin creates an **ingest token** via `POST /ingest-tokens`, specifying which provider adapter to use (default: `auto`).
2. The raw token (starts with `opsmender_ingest_...`) is returned **once** — save it. OpsMender stores only the SHA-256 hash.
3. External systems send JSON payloads to `POST /incidents/ingest` with the token in an `X-OpsMender-Token` header (or `Authorization: Bearer`).
4. OpsMender routes the payload through the chosen adapter (`auto` for universal, or a strict shape-specific adapter), which normalizes it into an incident.
5. For `auto` tokens: heuristic parse → LLM fallback on unrecognized shapes → per-token shape cache.
6. Dedup by `(external_source, external_id)` — repeated alerts update or skip instead of creating duplicates.
7. Every inbound payload is logged raw in the `ingest_log` table for replay/debugging.
8. Per-token rate limiting enforced (default: 60 req/min). Returns `429` with `Retry-After` header when exceeded.
9. Optional auto-start can create one session automatically for newly created incidents that match a configured source + minimum severity rule, but only when session-tier resolution yields Tier 0. Tier 1 and Tier 2 never auto-start.

### Supported provider adapters

| Provider | Key | Handles |
|----------|-----|---------|
| **Universal (auto-detect)** | `auto` | **Default.** Any JSON webhook — Slack, Datadog, Teams, Sumo Logic, Grafana, Alertmanager, custom scripts. Heuristics + LLM fallback with per-token shape cache. |
| CloudWatch | `cloudwatch` | SNS `SubscriptionConfirmation` + `Notification` envelopes with embedded alarm JSON |
| Azure Monitor | `azure_monitor` | Common alert schema v2 — maps severity (Sev0–4) and monitor condition |
| GCP Cloud Monitoring | `gcp_monitoring` | GCP incident webhook v1.2 — maps `state` (open/closed/acknowledged) |
| Oracle Cloud (OCI) | `oci_monitoring` | OCI alarm notifications — maps `status` (FIRING/OK/RESET) |
| Generic JSON | `generic` | Configurable dot-path field mapping — works with tools needing strict, deterministic parsing |

### Prometheus + Alertmanager example

```yaml
# alertmanager.yml
route:
  receiver: opsmender
  group_by: ["alertname"]
  group_interval: 30s

receivers:
  - name: opsmender
    webhook_configs:
      - url: "https://opsmender.example.com/api/v1/intake/svc_..."
        send_resolved: true
```

The same pattern works for any monitoring tool that can POST JSON: Datadog (webhook actions), CloudWatch (SNS HTTPS subscription with `cloudwatch` adapter), Azure Monitor (action group webhook), Sumo Logic (webhook payload), Grafana (contact point: webhook).

Full curl recipes covering the supported strict providers (CloudWatch SNS, Azure Monitor, GCP Monitoring, OCI, Generic), including lifecycle examples and severity mapping tables, live in [`docs/REFERENCE.md`](docs/REFERENCE.md#external-incident-ingestion).

## Notifications

OpsMender has **three distinct notification concepts**. They are separate from inbound alert ingestion (`POST /api/v1/intake/{service_token}`), which is how incidents get *in*.

| Concept | Audience | Purpose | Where |
|---|---|---|---|
| **Personal Routing** | The current on-call operator | How *one* person is paged for an incident they own (Slack/Teams DM, Email, SMS, Telegram DM, …) | Paging & On-call → My Notifications |
| **Notification Channels** | The responder team | Workspace/team channels where responders collaborate — this is where incident updates appear | Paging & On-call → Notification Channels |
| **Viewer Notifications** | Read-only stakeholders / downstream systems | Read-only session/incident updates to webhooks; never pages an operator | Paging & On-call → Notifications |

Personal Routing pages the owner; Notification Channels keep the responding team in the loop with formatted incident updates and, for opted-in Slack or Teams channels, verified native actions; Viewer Notifications fan out read-only status. The expected flow:

```
Alert intake → Incident created → Service/team/escalation chain resolved
  → Personal Routing pages the current on-call operator
  → Notification Channels receive the incident update (created/ack/resolved/escalated)
  → AI session start/completion/failure posts go to the same matching channels
  → Viewer Notifications receive read-only updates (if configured)
```

### Notification Channel capability model

Every platform is modelled honestly in [`backend/bots/capabilities.py`](backend/bots/capabilities.py) — the single source of truth for what a channel can actually do (`incident_updates`, `incident_card`, `interactive_actions`, `message_update`, `direct_message`, `shared_channel`, `delivery_only`). The API exposes it per platform and per configured channel, and the UI only advertises what a platform supports — a channel that receives lifecycle posts shows **"Incident updates"**, a future adapter with verified message edits can show **"Message updates"**, and only a verified callback adapter can show **"Interactive actions"**. Delivery-only channels (Twilio SMS, email, custom webhook) stay delivery-only and never claim native actions.

**Interactivity is enabled per verified adapter and channel.** On incident **created / acknowledged / resolved / escalated**, OpsMender posts a formatted incident message with an authenticated incident link. Slack and Microsoft Teams Notification Channels may opt into native **Acknowledge / Resolve / Escalate / Start AI Session** buttons. Slack clicks require a valid signing-secret HMAC/timestamp; Teams clicks require a valid Bot Framework JWT. Both then pass through the same durable replay protection, external identity mapping, active-user checks, Admin/Operator RBAC, action execution, and audit path. Other platforms continue to use the authenticated OpsMender link. AI session lifecycle posts are sent when an incident-linked session starts, completes, fails, or times out. OpsMender never embeds a public, unauthenticated action URL.

Notification Channels can be **workspace-wide** or scoped to one or more Teams. Incident ownership is resolved deterministically from the incident Service's team, then the active escalation chain's team, otherwise no team. Workspace-wide channels receive all incident/session lifecycle posts; team-scoped channels receive only matching incidents and sessions.

Email Notification Channels support **Mailgun Email** and outbound-only
**SMTP Email**. SMTP accepts hosted provider or internal relay configuration:
host/port, STARTTLS/implicit TLS/plain trusted relay, optional authentication,
sender, and recipient. This channel configuration is separate from optional
`OPSMENDER_SMTP_*` settings used for account invites and password resets. IMAP
and inbound SMTP callbacks are not supported.

**Message update-in-place (Slack):** as an incident progresses (acknowledged → resolved), Slack Notification Channels **edit the original incident card in place** via `chat.update` using the stored message timestamp, so the channel shows a single evolving card — current state plus action buttons — instead of a stack of messages. If Slack can no longer edit the message (edit window closed, message deleted), OpsMender posts a fresh follow-up message instead. Escalations always post a new message so they re-page. Microsoft Teams stays **follow-up-only**: Microsoft Graph app-only auth cannot edit a posted chat message's content, so Teams honestly posts a new message per lifecycle change rather than advertising an unsupported edit path.

**Future enhancements:** Discord/Telegram verified callbacks; bi-directional threaded chat and channel-to-OpsMender comment sync; MFA for action authorization; more platforms with first-class action support.

Legacy summary of the three flows:

- **Inbound**: external tools create incidents in OpsMender through a service endpoint: `POST /api/v1/intake/{service_token}`
- **Operator delivery**: OpsMender pages operators through configured channels and per-user routing
- **Viewer updates**: OpsMender notifies read-only/status recipients when a session is created, awaits approval, becomes active, completes, fails, or times out

Notification setup lives at `/dashboard/paging/notifications`. The legacy `/webhook-triggers` API remains available for viewer-update delivery. Each viewer update subscribes to one or more session events and uses one of four payload formats:

| Format | Purpose | Payload |
|--------|---------|---------|
| `generic` | Any automation endpoint | OpsMender normalized JSON event |
| `slack` | Slack incoming webhook | `text` + Block Kit `blocks` |
| `teams` | Microsoft Teams Workflows webhook | plain `text` body |
| `sumo` | Sumo Logic HTTP source / JSON ingestion endpoint | log-friendly JSON event with flattened top-level fields plus nested session/incident objects |

Supported events: `session.created`, `session.awaiting_approval`, `session.active`, `session.completed`, `session.failed`, `session.timed_out`, `*` for all.

---

## API endpoints

The full REST + WebSocket surface is documented in [docs/REFERENCE.md](docs/REFERENCE.md#api-endpoints). The most commonly used:

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/health` | — | Health check |
| `POST` | `/auth/login` | — | Log in, receive JWT |
| `GET` | `/auth/me` | Bearer | Current user profile |
| `POST` | `/incidents` | admin/operator | Create incident |
| `GET` | `/incidents` | any | List incidents (pagination, status filter) |
| `POST` | `/api/v1/intake/{service_token}` | URL token | Service Webhook — ingest incident from external source |
| `POST` | `/incidents/ingest` | Ingest token | Webhook — ingest incident from external source |
| `POST` | `/sessions` | admin/operator | Start a session |
| `GET` | `/approvals` | any | List approval requests |
| `POST` | `/approvals/{id}/approve` | admin/operator | Approve pending request |
| `POST` | `/approvals/{id}/reject` | admin/operator | Reject pending request |
| `GET` | `/audit` | any | Query audit entries (filters + pagination) |
| `WS` | `/sessions/{id}/stream?token=JWT` | JWT query param | Live session streaming |

### Roles

| Role | Permissions |
|------|-------------|
| `admin` | Full access — create incidents, sessions, update config |
| `operator` | Create incidents and sessions, read config |
| `viewer` | Read-only access to incidents, sessions, audit |

---

## Project structure

```
OpsMender-AI/
├── backend/
│   ├── agent/              # LangGraph workflow nodes, state, execution wiring
│   ├── api/                # FastAPI app, schemas, auth dependencies, route modules
│   ├── approvals/          # Tier 1 approval wait/timeout flow
│   ├── audit/              # Audit logging and audited tool execution
│   ├── auth/               # Local auth, OIDC, SAML, invites, bootstrap helpers
│   ├── bots/               # Chat-ops connector models and webhook handlers
│   ├── db/                 # SQLAlchemy models, repos, Alembic migrations
│   ├── ingest/             # External incident ingestion and universal adapter
│   ├── llm/                # BYOM provider abstraction, registry, factories
│   ├── mcp/                # MCP clients, OAuth, mcp.json import/export/sync
│   ├── memory/             # Incident memory retrieval, writeback, compaction
│   ├── paging/             # Services, teams, rosters, escalation, notifications
│   ├── retention/          # Storage retention config and pruning scheduler
│   ├── skills/             # SKILL.md parser and startup auto-importer
│   ├── sla/                # Reliability targets, uptime samples, SLA polling
│   ├── tiers/              # Programmatic tier enforcement
│   └── workflow/           # Saved workflow profile validation
├── cli/                    # `opsmender` CLI entry point
├── deploy/
│   ├── cloud/              # AWS ECS, Azure Container Apps, GCP Cloud Run, OCI recipes
│   └── helm/               # Kubernetes Helm chart
├── docker/                 # Single-container image and compose recipe
├── docs/
│   ├── REFERENCE.md        # Architecture, APIs, data model, decisions
│   ├── paging-model.md     # Paging/on-call model reference
│   └── wiki/               # Operator and admin guides
├── examples/               # Reference SKILL.md
├── frontend/
│   ├── app/dashboard/      # Next.js dashboard routes
│   ├── components/         # Command Center, paging, config, tables, shell
│   └── lib/                # API client, types, tenant helpers
├── scripts/                # Build, seed, dev, and wiki helper scripts
├── skills/                 # Operator-owned skill files auto-imported on startup
├── tests/                  # Backend unit, API, integration, and E2E tests
├── .env.example            # Documented self-hosted configuration template
├── opsmender.spec          # PyInstaller binary spec
└── pyproject.toml          # Python package, CLI, and dependency metadata
```

---

## Contributing / developer workflow

This section is for people **editing the code** — not for operators running OpsMender. For installation, use the Docker Compose paths above.

### Requirements

- [uv](https://docs.astral.sh/uv/) (Python package manager) and Python 3.11+
- Node.js 20+ and npm (for the frontend build)
- No Postgres required for local iteration — the dev launcher falls back to SQLite

### One-time setup

```bash
git clone https://github.com/SpicyDaemon/OpsMender-AI.git
cd OpsMender-AI

uv sync --dev                            # backend deps + register `opsmender` CLI
cp .env.example .env                     # leave OPSMENDER_DEPLOYMENT_MODE=development
cd frontend && npm install && cd ..      # frontend deps
```

### Running the dev server (single-port, SQLite)

```bash
cd frontend && npm run build && cd ..    # one-time, or after frontend changes
uv run python scripts/dev_server.py
```

Open **http://localhost:8000** and log in with `admin` / `admin123`. The dev launcher uses `Base.metadata.create_all` (no Alembic), seeds the admin, and serves the static frontend through FastAPI.

### Hot-reload workflow (frontend iteration on port 3000)

When you're actively editing React, the static-build cycle is painful. Run the Next.js dev server on its own port and point it at the backend:

```bash
# one-time: tell the Next.js dev server where the API lives
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > frontend/.env.local

# terminal 1 — backend only
uv run python scripts/dev_server.py

# terminal 2 — frontend dev server (hot reload)
cd frontend && npm run dev
```

Open **http://localhost:3000**. The frontend calls the API on `:8000` using the env var above. This is the only path that uses port 3000 — every other deployment surface serves the frontend from the backend on port 8000.

> **Why the `.env.local` step matters:** `frontend/lib/api.ts` defaults `BASE_URL` to same-origin so the production single-process build (port 8000) works out of the box. In dev with two separate processes, the frontend on `:3000` would otherwise call itself for `/auth/login` and 404.

### Running tests

```bash
uv run pytest              # full suite
uv run pytest -xvs         # verbose, stop on first failure
uv run pytest tests/test_api.py       # API layer tests
uv run pytest tests/test_workflow.py  # workflow tests
```

### End-to-end verification

`tests/test_e2e.py` is the canonical "does OpsMender still work?" check — it drives the full single-container chain operators actually use, with no external services touched:

```
register/login → POST /incidents → POST /sessions → tier gate creates approval
            → POST /approvals/{id}/approve (or /reject) → gate resumes
            → mocked MCP call → PgAuditLogger writes rows → GET /audit
```

`tests/test_e2e_paging_flow.py` is the canonical paging-loop check — it drives the entire flow through real HTTP routes (ingest → priority → page → chain → Slack ack → web force-takeover → Slack resolve).

`tests/test_frontend_mount.py` covers the static frontend mount + SPA fallback served by FastAPI (the same path the binary and Docker image use).

Run together:

```bash
uv run pytest tests/test_e2e.py tests/test_frontend_mount.py -v
```

Expected: 9 passed (2 E2E flows — approve + reject — plus 7 frontend mount cases).

If you change anything in the API layer, the workflow, the approval service, the audit logger, or the static frontend mount, those are the tests that prove the whole chain still composes.

### Responsive incident-detail verification

A repeatable viewport sweep for the incident detail page and its sticky command strip. It drives a real browser against a live local stack, captures screenshots at `320`, `375`, `768`, and `1440` widths, and fails if the page introduces horizontal overflow or loses the primary action set.

```bash
# one-time on a machine that hasn't used Playwright before
cd frontend && npx playwright install chromium

# terminal 1
uv run python scripts/dev_server.py

# terminal 2
cd frontend && npm run test:incident-responsive
```

Screenshots and metrics are written to `frontend/artifacts/incident-detail-responsive/` and are gitignored.

### CLI

The `opsmender` CLI is registered as a `[project.scripts]` entry point by `uv sync` — call it via `uv run opsmender …` (or activate the venv).

| Command | Description |
|---------|-------------|
| `opsmender --version` | Show version |
| `opsmender check` | Validate config and test MCP server connectivity |
| `opsmender serve` | Start the API and embedded static frontend |
| `opsmender run --incident "desc"` | Run a full incident response session |
| `opsmender run --dry-run --incident "desc"` | Dry-run (no LLM, no MCP) |
| `opsmender audit` | View the audit log (human-readable table) |
| `opsmender audit --last N` | Show the last N audit entries |
| `opsmender audit --json` | Output as raw JSONL |
| `opsmender config` | Show current configuration summary |
| `opsmender config model list` | Discover provider availability and reported models |
| `opsmender config model set --provider ... --model-id ...` | Validate and persist the default model config |
| `opsmender config model bootstrap` | First-run bootstrap for the default model config |
| `opsmender approvals list` | List approval requests |
| `opsmender approvals approve ID` | Approve a pending Tier 1 request |
| `opsmender approvals reject ID` | Reject a pending Tier 1 request |
| `opsmender mcp export [--path P]` | Export the current DB MCP server state to mcp.json |
| `opsmender mcp reload [--apply] [--prune]` | Reconcile mcp.json into the DB |

### Common contributor gotchas

- **`GET / → 404 Not Found`** in the dev-server log: `frontend/out/` doesn't exist. Run `cd frontend && npm run build` first.
- **Login or any API call returns 404 from `http://localhost:3000`**: `frontend/.env.local` is missing or doesn't set `NEXT_PUBLIC_API_URL`. Restart `npm run dev` after creating it.
- **CSS / `@theme` token changes don't appear after hot-reload (Tailwind v4 + Turbopack):** stop `npm run dev`, `rm -rf frontend/.next`, restart.
- **`Connect call failed ('127.0.0.1', 5432)`** at startup: `.env` has `OPSMENDER_DATABASE_URL=postgresql+asyncpg://…` but no Postgres is running. Comment that line out so the SQLite fallback engages.
- **Code changes not picked up:** `dev_server.py` runs Uvicorn with `reload=False`. Stop and restart after backend edits.
- **Port 8000 in use:** `lsof -i :8000` (Linux/macOS) or `netstat -ano | findstr :8000` (Windows) to find the PID, then kill it.
- **SQLite on Python 3.14.x:** use a file URL (`sqlite+aiosqlite:///./opsmender-local.db`). In-memory (`sqlite+aiosqlite://`) hangs.

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch / PR conventions and architectural guardrails.

---

## Distribution status

OpsMender ships as a self-hosted project with several supported paths:

- **Docker Compose:** fastest full-stack local or single-host install.
- **Single container:** backend + exported frontend served by `opsmender serve`.
- **Helm:** Kubernetes deployment with optional bundled Postgres; JWT secret auto-generated on first install and preserved across upgrades.
- **PyInstaller binaries:** standalone `opsmender` executables published on every `v*` tag for Linux x64 and Windows x64, with matching `.sha256` checksums.
- **Cloud recipes:** AWS ECS, Azure Container Apps, GCP Cloud Run, and OCI Container Instances.

See [docs/REFERENCE.md](docs/REFERENCE.md) for full architecture details and the complete decision log.
