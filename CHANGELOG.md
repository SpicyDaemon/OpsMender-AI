# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Sprint 36 step 1 — Slack page card + interactivity endpoint** (Session 078). First slice of the structured-paging-UX sprint. New `backend/paging/slack_cards.py` builds Block Kit JSON for an actionable page card: priority emoji + status badge + truncated description in the section header, an Acknowledge / Take Over / Resolve action row, and (when `OPSMENDER_PUBLIC_URL` is set) a "View in OpsMender" button that deep-links to `/dashboard/incidents/detail?id={id}&from=slack`. Action ids use a stable `opsmender:{ack,take,resolve,view}` shape; the incident UUID rides in `actions[0].value` and the `block_id` for redundancy. New `backend/api/routes/slack_paging.py` exposes `POST /bot/slack/interactions`: verifies every request with the Slack signing secret stored on the matching `bot_connectors` row (replay window 5 min), parses the form-encoded `payload`, resolves the Slack user via `BotUserLinkRepo.get_by_platform_user`, and routes block_actions to `handle_ack` / `handle_takeover_request` / `cancel_chain` + `IncidentRepo.update_status('resolved')`. Unlinked Slack users get a friendly ephemeral "your Slack account isn't linked" message instead of silent failure. `Channel.send` gained an optional `blocks` kwarg (other channels accept and ignore it); `SlackDMChannel` posts the Block Kit card alongside the fallback `text`, and `dispatch_page` builds the card whenever it fans out to `slack_dm`. 9 new tests in `tests/test_slack_paging.py` (`TestPageCardBuilder` × 4 + `TestSlackInteractionsEndpoint` × 5). Full suite: **975 passed, 2 skipped** (+9 vs Session 077's 966).
- **Sprint 35 engine → dispatcher wiring** (Session 077). The escalation chain engine now auto-invokes `dispatch_page` for every step it fires, so a paged user actually gets a Slack DM / Teams message / Email / SMS without any extra wiring at the call site. New `backend/paging/channel_factory.py` reads global channel credentials from env (`OPSMENDER_SLACK_BOT_TOKEN`, `OPSMENDER_TEAMS_WEBHOOK_URL`, `OPSMENDER_SMTP_HOST` + SMTP family, `OPSMENDER_TWILIO_ACCOUNT_SID` + Twilio family) and returns a `ChannelFactory`; channels with unset credentials yield `None` and the dispatcher records a `skipped` row with reason `channel_unconfigured`. `_fire_step` / `start_chain` / `tick` / `tick_all_due` all accept an optional `channel_factory` (default `None` keeps Sprint 34 record-only behavior for tests). Both incident-creation kickoff sites (REST route + ingest service) and the `EscalationScheduler` background loop now pass `build_channel_factory()` so production paging fans out end-to-end. 5 new tests in `tests/test_escalation.py` cover the wiring: `TestEngineDispatchWiring` (factory present → `recorded` audit anchor **and** `slack_dm`/`sent` rows + a real `MockTransport` chat.postMessage call; factory absent → only the audit anchor) and `TestChannelFactoryBuilder` (unconfigured env → all-`None`, partial Twilio → no SMS, complete Twilio → `SMSChannel`). Full suite: **966 passed, 2 skipped** (+5 vs Session 076's 961).
- **Sprint 35 notification API + frontend** (Session 076). Steps 7–11 close out the operator-facing surface for Sprint 35. New routes: `GET/PUT /users/me/notification-preferences` (auto-creates an empty row on first GET); `GET/PUT /organizations/{id}/notification-settings` (admin-only — `notification_dedup_window_minutes`, 0–1440). The `/maintenance-windows` schema gains `description`, `scope_type`, `scope_id`; `target_ids` defaults to empty so paging-style windows no longer need the SLA-era stub. `/incidents/{id}/paging` now joins through `suppressed_by_maintenance_window_id` and returns a `SuppressedByMaintenanceWindow` block when the incident was suppressed. Frontend adds two new tabs to `/dashboard/paging`: **Maintenance Windows** (Active/Scheduled/Past sub-tabs with counts, From/To range filter, scope-aware create modal for global/service/roster/team) and **My Notifications** (per-channel toggle + destination inputs for Slack DM / Teams DM / Email / SMS, P0–P3 × channel routing matrix that disables until the channel is enabled, quiet-hours panel with start/end, time zone, and `min_priority_to_break`). The incident detail page now fires `getIncidentPaging` alongside `getIncident` and renders an amber "Paging suppressed by maintenance window" banner above the title when the window is set. End-to-end `TestDispatchEndToEnd::test_slack_dm_via_mock_transport` ties `dispatch_page` to a real `SlackDMChannel` via `httpx.MockTransport`. Full suite: **961 passed, 2 skipped** (+14 vs Session 075's 947).
- **Sprint 35 dispatcher + channels** (Session 075). `backend/paging/dispatch.py` ships a single entry point `dispatch_page(...)` that runs the full notification pipeline: maintenance-window suppression (page → suppressed; `escalate_immediate` never downgraded; stamps `incidents.suppressed_by_maintenance_window_id`) → per-user prefs lookup → quiet-hours block with `min_priority_to_break` override → per-channel dedup (within `organizations.notification_dedup_window_minutes`) → channel `send()` → one `incident_pages` row per attempt with `delivery_status` + error reason. `backend/paging/channels.py` ships the four v1 transports: `SlackDMChannel` (chat.postMessage), `TeamsDMChannel` (MessageCard webhook), `EmailChannel` (stdlib smtplib via `asyncio.to_thread`), `SMSChannel` (Twilio Messages API). All HTTP channels accept a `http_client_factory` so tests inject `httpx.MockTransport`; `EmailChannel` accepts a `smtp_factory` for stub SMTP servers. New `IncidentPageRepo.has_recent_delivery` query backs the dedup check. 25 new tests in `tests/test_paging_dispatch.py`. Full suite: **947 passed, 2 skipped**.
- **Sprint 35 schema foundation** (Session 074). Paging notification plumbing now has the persistence layer it needs: `organizations.notification_dedup_window_minutes` (default 10), scoped maintenance windows (`scope_type` / `scope_id` on the existing SLA `maintenance_windows` table), `incidents.suppressed_by_maintenance_window_id`, and new `user_notification_prefs` rows keyed by user + org with channels, priority routing, and quiet-hours JSON. Added repository helpers and tests for scoped window lookup, notification preference upsert, and org dedup settings. Added `tzdata` as a runtime dependency so IANA time zones work consistently on Windows.
- **Platform-agnostic Environment Scans** (post-Sprint-34, Session 073). Major principle clarification + UX simplification: the framework now ships **zero** hard-coded knowledge of any specific runtime platform (Kubernetes, ECS, Cloud Run, Azure Container Apps, OCI Container Instances, Nomad, systemd, …). New locked decision **D-023** in `docs/REFERENCE.md`. Concrete changes:
  - The three-analyzer registry collapsed to one: `kube-score` and `istioctl-analyze` adapters moved to `backend/auditor/example_analyzers.py` (reference only, no auto-register). The default `EnvironmentScanAnalyzer` (renamed from `GenericLLMAnalyzer`, key `environment-scan`) is the only built-in. Operators who want deterministic per-platform output register example adapters explicitly at startup.
  - **SKILL.md gains an optional `focus_areas:` list.** Free-form, platform-neutral phrases ("crashlooping containers", "tasks stuck in PROVISIONING", "high systemd restart counts") that the LLM weights its scan toward. `SkillDefinition.focus_areas` exposed via `SkillResponse.focus_areas`. Comma-separated strings accepted too.
  - **Environment Scans UI redesign.** The analyzer chip selector is gone. After picking an MCP server, the user sees focus-area toggle chips drawn from that server's SKILL.md (or "No focus areas configured. The LLM will pick what to scan." with a pointer to add them). One "Run scan" button.
- **Simplification pass** (post-Sprint-34, Session 072). Six UX gaps addressed before pushing further into the roadmap:
  - **Renamed routes + nav.** `/dashboard/audits` → `/dashboard/scans` (label: **Environment Scans**) and `/dashboard/audit` → `/dashboard/activity` (label: **Activity**) so the AI-action history and the read-only environment scans stop sharing the word "audit". Keyboard shortcut `Alt+L` now opens Activity.
  - **Detectors marked "Legacy"** in the sidebar with a colored pill — Sprint 39 deletes the surface; new users should invest in Environment Scans instead.
  - **MCP server picker on Environment Scans.** Every scan now requires picking an MCP server up front, and that server's name is auto-passed as `mcp_server_name` to every selected analyzer. The earlier failure mode (analyzer returns "Analyzer requires `mcp_server_name` in params") is no longer reachable through the UI. Helper copy makes the read-only / skill enforcement guarantee explicit.
  - **Paging "How it works" diagram.** New info button on the Paging page opens an inline SVG flow showing the full pipeline (inbound alert → priority rules → response mode → service → escalation chain → step targets → roster on-call resolution → ack / takeover / hard timeout). Replaces having to read `docs/paging-model.md` for first-pass understanding.
  - **Per-service ingest tokens.** `ingest_tokens` gains a nullable `service_id` (Alembic `a4b5c6d7e8f9`). When a token is bound to a service, every incident it creates gets `service_id` pre-filled, which lets the Sprint 34 escalation engine pick the owning team's chain without the alert payload having to encode the service. `IngestTokenCreate` accepts the new field.
  - **Approvals page → recovery surface.** Every row now has an explicit "Open session →" link so a user who closed the chat tab can come back to the Approvals page and jump straight to the conversation. The session detail page already had inline Approve / Reject cards from Sprint 9, so the Approvals page becomes the catch-up inbox rather than a mandatory hop; empty-state copy now says so.
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
