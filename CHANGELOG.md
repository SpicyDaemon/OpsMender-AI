# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-04-22

First public release. MIT-licensed. Complete feature set from Sprints 1–23.

### Added

#### Core framework (Phase 1)
- Tiered access control (Tier 0–3) enforced programmatically at every tool call.
- MCP-first infrastructure integration — three transports (`stdio`, `sse`, `http`) with optional bearer-token auth.
- Skill definition system — org-owned `SKILL.md` classifies each operation as `safe`, `caution`, or `destructive`. Cannot be overridden by the agent.
- LangGraph incident-response workflow: `observe → diagnose → plan → tier_gate → execute → verify → summarize`.
- `aim` CLI — `run`, `check`, `config`, `audit`, `approvals`, `serve`.
- JSONL audit logger capturing every node transition and every tool call.

#### API + persistence (Phase 2)
- FastAPI REST + WebSocket API, JWT auth, three roles (`admin`, `operator`, `viewer`).
- PostgreSQL persistence via SQLAlchemy + Alembic (SQLite fallback for local dev).
- Tier 1 human-in-the-loop approval flow — workflow pauses, UI/CLI approve/reject, configurable timeout, WebSocket push.
- BYOM provider abstraction — Anthropic, OpenAI, Azure OpenAI, Ollama (local).
- Next.js 16 dashboard — incidents, live session view, audit log, approvals, config, co-pilot chat.
- Co-pilot chat — real-time parallel LLM channel that doesn't block the workflow.
- Single-container deployment — `aim serve` runs the FastAPI API plus the embedded static frontend. Docker image bundles Node.js so MCP servers shipping as `npx` packages work out of the box.
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
- Dashboard and `aim config model bootstrap` CLI path for entering provider, model ID, API-key env-var reference, temperature, max tokens, base URL, and API version.
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
- Node.js LTS bundled inside the Docker image for `npx`-based MCP servers. `AIM_NODE_PATH` override supported for binary installs.

[Unreleased]: https://github.com/SpicyDaemon/OpsMender-AI/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/SpicyDaemon/OpsMender-AI/releases/tag/v1.0.0
