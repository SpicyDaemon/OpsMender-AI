# OpsMender AI (OpsMender) Documentation

Welcome to the OpsMender AI (OpsMender) Wiki! This documentation is designed to help operators, administrators, and incident commanders deploy, configure, and use OpsMender effectively.

The dashboard now deep-links back into these guides from its empty states, so the pages below are also the contextual "what do I do next?" surface inside the product.

If you are a developer looking for architecture details or codebase references, please see the inline module documentation in the source tree (e.g. `backend/agent/`, `backend/paging/`, `backend/tiers/`).

## Table of Contents

### 1. Introduction & Setup
* [Getting Started](getting-started.md) — Installation, your first login, and running your first AI-assisted incident session.

### 2. Administrator Guide
* [Administrator Guide](admin-guide.md) — Runtime configurations, LLM providers, setting up MCP servers, notification channels, outbound hooks, and alert intake.
* **Authentication** — start here, then branch:
  * [Auth Guide](auth-guide.md) — **default model**: single workspace, email + password, admin-issued invites, three roles. What 95% of self-hosted installs use.
  * [People Guide](people-guide.md) — day-to-day People-page operations: invites, password resets, auth-method badges, deactivation vs soft delete, bootstrap admins, SMTP.
  * [Advanced Auth Guide](advanced-auth-guide.md) — optional surfaces: OIDC, SAML 2.0, custom-domain login behavior, and the `OPSMENDER_ADVANCED_AUTH_ENABLED` setup flag.
* [Integrations Guide](integrations-guide.md) — Encrypted external-system connectors, tier-governed actions, alert intake, and notification channels.
* [Skills Guide](skills-guide.md) — Managing MCP servers, enforcement tiers, and capability examples.
* [MCP Skills (Skill Studio)](mcp-skills.md) — The MCP Skills builder: per-tier classification, deny lists, the generic-command guardrail, templates, and the invariant that the backend tier gate is the execution authority. (Deep-linked from the in-product Skills page.)

### 3. Operator Guide
* [Operator Guide](operator-guide.md) — Incident triage flow, managing approvals, interacting with session chat, using the audit log, and understanding rollback behavior.
* [Paging Guide](paging-guide.md) — Top-level orientation: the incident-response loop, services / teams / rosters / chains / priority rules / response modes / maintenance windows / notification preferences, a setup walkthrough, and an end-to-end verification recipe.
* [Notification Preferences](notification-preferences.md) — My Routing, Respond/Track Notification Channels, quiet hours, dedup, maintenance windows, and the reports-only Inform model.
* [Slack as your paging surface](slack-paging-surface.md) — Block Kit page cards, slash commands (`/ack` / `/take` / `/release` / `/resolve` / `/snooze` / `/status`), per-incident channel mirroring, and Slack app setup.
* [Teams as your paging surface](teams-paging-surface.md) — Adaptive-card page cards with Acknowledge / Resolve / Escalate / Start AI Session actions via the Microsoft Bot Framework, Graph app-only OAuth setup, and a verification recipe.
* [Responding from your phone](mobile-incident-response.md) — Which OpsMender pages are mobile-optimized, the chat → web UI flow on phones, and a real-device verification checklist.
* [AI Incident Memory](memory-guide.md) — How OpsMender continuously carries lessons across incidents, immediately recalls validated memories, compacts them independently per service, and supports team-scoped edit/single/bulk deletion. The session detail page shows exactly which memories shaped each session.
* [Postmortems](postmortem-guide.md) — Per-incident postmortem editor on the Incident Command Strip, the seven canonical sections (Summary · Impact · Timeline · Root cause · Resolution · Lessons learned · Memory candidates), Edit/Preview toggle, role permissions, REST surface, and how memory candidates feed back into the AI memory curation flow.
* [Reliability](reliability-guide.md) — HTTP/HTTPS target checks, uptime/outage views, recent response time, 365-day response-time history, and latency retention behavior.
* [Troubleshooting](troubleshooting.md) — Solutions for common login, connectivity, and configuration issues.
