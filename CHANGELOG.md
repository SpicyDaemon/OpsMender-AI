# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Escalation chains + ack lifecycle** (Sprint 34). New tables: `escalation_chains`, `escalation_steps`, `service_escalation_chains`, `incident_pages`, `incident_chain_states`. New chain execution engine `backend/paging/escalation.py` with `start_chain` / `tick` / `handle_ack` / `handle_takeover_request` / `handle_takeover_confirm` / `handle_force_takeover` / `cancel_chain` / `tick_all_due`. Additive paging per D-021 #5 — once paged, stay paged. Soft-takeover with 5-minute window, hard inactivity timeout at 15 minutes, admin force-takeover (audit-logged via `assigned_by="admin_force"`). New `EscalationScheduler` runs every 10s during app lifespan and advances chains whose timers have expired — idempotent / restart-safe via the `status` column. New routes: `GET/POST /escalation-chains`, `PUT/DELETE /escalation-chains/{id}`, `GET/POST /escalation-chains/{id}/steps`, `DELETE /escalation-chains/{id}/steps/{step_id}`, `POST/DELETE /services/{id}/escalation-chains[/{chain_id}]`, `GET /incidents/{id}/chain`, `POST /incidents/{id}/ack`, `POST /incidents/{id}/take` (request / confirm / force). Chain auto-kicks off at incident creation when `response_mode` is `page` or `escalate_immediate` (REST + ingest paths). New Escalation Chains tab on `/dashboard/paging` with inline step editor. Notification channel delivery (Slack/Teams/Email/SMS) remains in Sprint 35; v1 chain runs are testable via the `incident_pages` audit log alone.
- **Paging foundation** (Sprint 33). First slice of OpsMender-owned paging (D-021). New tables: `teams`, `team_members`, `services`, `rosters`, `roster_members`, `roster_overrides`, `service_rosters`, `priority_rules`, `priority_llm_override_log`, `incident_assignments`. New incident columns: `priority` (P0–P3), `response_mode` (`auto_resolve`/`notify`/`page`/`escalate_immediate`), `service_id`. Pure-function modules `backend/paging/on_call.py` (`on_call_at` — deterministic rotation math + override + handoff-time boundary + IANA tz) and `backend/paging/priority.py` (`assign_priority` — first-match-wins rules + one-way LLM escalation). New routes: `/teams`, `/teams/{id}/members`, `/services`, `/rosters`, `/rosters/{id}/members`, `/rosters/{id}/overrides`, `/rosters/{id}/on-call`, `/priority-rules`, `/incidents/{id}/paging`, `/incidents/{id}/assign`, `/incidents/{id}/release`. Priority + response mode are computed at incident creation time (both REST and inbound ingest paths) and locked thereafter. Incident assignment grants incident-scoped operator authority per D-021 #9 — an assignee can release without being a global admin/operator. New `/dashboard/paging` page (tabbed: Teams / Services / Rosters / Priority Rules). Escalation chains, maintenance windows, notification preferences, and channel fan-out are deferred to Sprints 34–35.
- **Auditor v1** (Sprint 32). New `/audits/*` surface separates *latent* findings from *paging* incidents. `POST /audits/runs` kicks off a scan across one or more analyzers; findings land in `audit_findings` (not `incidents`) and stay quiet until a human triages them. Three built-in analyzers ship: `kube-score`, `istioctl-analyze`, and `generic-llm-analyzer` (LLM-driven fallback for any read-only MCP server). Each finding carries a "Fix with AI" action that spawns a tier-gated session with the finding text as the goal prompt. New tables: `audit_runs`, `audit_findings`. New routes: `GET /audits/analyzers`, `POST/GET /audits/runs`, `GET /audits/runs/{id}`, `GET /audits/findings`, `POST /audits/findings/{id}/{remediate,dismiss}`. New page: `/dashboard/audits`. Detectors stay running through this sprint; deprecation lives in Sprint 39.
- **Bot Connector UX overhaul** (Sprint 31). The Bot Connectors modal now renders a typed, per-platform form instead of two raw JSON boxes. Every adapter publishes a `FieldSpec` schema (label, kind, required, helper text, doc URL) through `GET /bot-connectors/platforms[/{platform}/schema]`. Secret fields use password masks with show/hide toggles; required fields gate the Save button; an inline "Test connection" button calls the existing `POST /bot-connectors/{id}/test` and surfaces pass/fail in the modal. All 15 registered adapters carry typed schemas. Platforms without a schema fall back to the legacy JSON UI.
- **Slack / Discord OAuth install** (Sprint 31 Step 5–6). When `OPSMENDER_SLACK_OAUTH_CLIENT_ID` / `OPSMENDER_SLACK_OAUTH_CLIENT_SECRET` (or Discord equivalents) are set in env, the modal shows a "Connect to Slack/Discord" button that walks the operator through the provider's consent screen and writes the bot token back automatically. New routes: `GET /bot-connectors/oauth/{platform}/start` (returns `{authorize_url}`) and `GET /bot-connectors/oauth/{platform}/callback`. State is a signed JWT (5-min TTL) carrying `connector_id` so callbacks can't be cross-pollinated. OAuth populates the bot token only — signing secrets remain a manual paste because providers don't return them through OAuth.
- **Per-tenant SAML 2.0 SSO** (Sprint 30). New `org_saml_configs` table next to `org_sso_configs`. Admin CRUD under `/organizations/{id}/saml`. SP-initiated flow under `/auth/saml/{slug}/login` + `/acs` + `/metadata`. IdP described by metadata URL (auto-fetched + cached) or raw XML paste. Global SP keypair via `OPSMENDER_SAML_SP_CERT` / `OPSMENDER_SAML_SP_KEY` (use `opsmender saml gen-sp-keys` to generate). JIT user provisioning, `allowed_email_domains` allowlist, signed-AuthnRequest. SLO and encrypted assertions are out of scope for this drop. Docker image installs `libxmlsec1` / `libxml2` runtime libs; PyInstaller binary fails-loud if those libs are missing on the host.
- **Helm chart** for Kubernetes deployments at `deploy/helm/opsmender/`. App Deployment/Service/Ingress, optional bundled Bitnami Postgres subchart or external DB, persistent `/app/logs` PVC, ConfigMap + Secret env wiring, liveness/readiness probes, optional HPA.
- TopBar: active org name now visible at all breakpoints with a `host-pinned` qualifier in the user menu when Domain Isolation is in effect.
- TopBar: per-org role (admin / operator / viewer) now shown as a colour-coded pill next to the active org name in the org switcher, host-pinned badge, and user dropdown panel. Source is the per-org role from `listMyOrganizations`, since role can differ per tenant.

### Fixed
- Dev seed (`scripts/dev_server.py`) now idempotently creates the default "Main" organization, sets `users.primary_org_id`, and inserts the `user_organizations` link row for the seeded admin. Older local DBs that pre-date multi-tenancy get backfilled on every startup.
- Static SPA catch-all in `backend/api/static.py` now accepts both `GET` and `HEAD`. Previously HEAD requests from health checkers, link previewers, and prefetchers got a default 405 from FastAPI.
- Static SPA catch-all now rewrites Next.js 16 RSC prefetch URLs (`/<route>/__next.<a>.<b>.<c>.txt`) to their nested on-disk form (`/<route>/__next.<a>/<b>/<c>.txt`). Without this, client-side route prefetches 404'd on the FastAPI-served production image.

## [1.0.0] — 2026-04-22

First public release. MIT-licensed. Complete feature set from Sprints 1–23.

### Added

#### Core framework (Phase 1)
- Tiered access control (Tier 0–3) enforced programmatically at every tool call.
- MCP-first infrastructure integration — three transports (`stdio`, `sse`, `http`) with optional bearer-token auth.
- Skill definition system — org-owned `SKILL.md` classifies each operation as `safe`, `caution`, or `destructive`. Cannot be overridden by the agent.
- LangGraph incident-response workflow: `observe → diagnose → plan → tier_gate → execute → verify → summarize`.
- `opsmender` CLI — `run`, `check`, `config`, `audit`, `approvals`, `serve`.
- JSONL audit logger capturing every node transition and every tool call.

#### API + persistence (Phase 2)
- FastAPI REST + WebSocket API, JWT auth, three roles (`admin`, `operator`, `viewer`).
- PostgreSQL persistence via SQLAlchemy + Alembic (SQLite fallback for local dev).
- Tier 1 human-in-the-loop approval flow — workflow pauses, UI/CLI approve/reject, configurable timeout, WebSocket push.
- BYOM provider abstraction — Anthropic, OpenAI, Azure OpenAI, Ollama (local).
- Next.js 16 dashboard — incidents, live session view, audit log, approvals, config, co-pilot chat.
- Co-pilot chat — real-time parallel LLM channel that doesn't block the workflow.
- Single-container deployment — `opsmender serve` runs the FastAPI API plus the embedded static frontend. Docker image bundles Node.js so MCP servers shipping as `npx` packages work out of the box.
- PyInstaller binary build (`scripts/build_binary.sh`).

#### Ingestion + detection (Sprints 14–15)
- Inbound webhook ingestion — CloudWatch/SNS, Azure Monitor, LegacyAlertVendor, LegacyAlertRelay, generic JSON.
- Universal adapter (`auto` provider) — heuristics + LLM-fallback shape learning so new tools work without a bespoke adapter. Per-token shape cache.
- External fingerprint dedup — repeated alerts update the existing incident.
- Rate limiting per ingest token. Raw-payload audit log for replay.
- MCP-driven incident detector — scheduled rules ask the agent to inspect an MCP server and auto-file an incident when it spots a problem. Read-only enforced by locked skill profile. Cost guardrails per rule + global.

#### Safety controls (Sprint 17)
- Tier 0 sandbox — spawn-time MCP tool allowlist, rollback-safe-ops-only runtime guard.
- Tier 0 hard time limits — per-session and per-node wall clocks.
- Automatic compensating-inverse rollback on Tier 0 failure or timeout. Admin "Rollback session" UI + API.
- `reversible` + `compensating_inverse` skill metadata enforced at the tier gate.

#### Outbound integrations (Sprints 18–21)
- Outbound webhook triggers for session lifecycle (`session.created`, `session.awaiting_approval`, `session.active`, `session.completed`, `session.failed`, `session.timed_out`).
- First-class payload formats: `generic`, `slack`, `teams`, `sumo`.

#### Workflow + agent customization (Sprints 22–23)
- Custom workflow builder — saved workflow profiles with ordered node lists. Tier gate always immediately precedes execute. Profiles picked at session start.
- Multi-agent support — saved agent team profiles run specialist-role passes plus a final synthesis pass. Single-path execution; no arbitrary parallel branches.

#### Model onboarding + bootstrap (Sprint 24)
- First-run model setup no longer depends on pre-seeded `.env` values.
- Dashboard and `opsmender config model bootstrap` CLI path for entering provider, model ID, API-key env-var reference, temperature, max tokens, base URL, and API version.
- Manual model IDs accepted when discovery is stale or unavailable — warnings returned, not hard failures.
- `GET /models/bootstrap` API exposes first-run state for the dashboard.

### Security

- Bearer-token auth for inbound ingest endpoints (JWT reserved for dashboard / API users).
- Webhook trigger stored headers/tokens preserved on edit unless the admin explicitly clears them.
- Env-var-reference-only secret model for model configs — API keys never persisted in the DB.

### Docs

- `docs/REFERENCE.md` — architecture, data model, tier semantics, rollback semantics, ingest provider recipes (CloudWatch, Azure Monitor, LegacyAlertVendor, LegacyAlertRelay, generic + universal adapter), detector model, webhook event payloads.
- `docs/TASKS.md`, `docs/CURRENT_STATE.md`, `docs/LOGS.md`, `docs/PROMPT_CONTEXT.md` — development workflow.

### Infrastructure

- Docker Compose for the full stack (`docker/docker-compose.yml`).
- Single multi-stage production Dockerfile (`docker/Dockerfile`) with health checks and a logs volume mount.
- Node.js LTS bundled inside the Docker image for `npx`-based MCP servers. `OPSMENDER_NODE_PATH` override supported for binary installs.

[Unreleased]: https://github.com/SpicyDaemon/OpsMender-AI/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/SpicyDaemon/OpsMender-AI/releases/tag/v1.0.0
