# OpsMender AI (OpsMender) Documentation

Project website: **[opsmenderai.com](https://opsmenderai.com)**

Welcome to the OpsMender AI (OpsMender) Wiki! This documentation is designed to help operators, administrators, and incident commanders deploy, configure, and use OpsMender effectively.

The dashboard now deep-links back into these guides from its empty states, so the pages below are also the contextual "what do I do next?" surface inside the product.

If you are a developer looking for internal architecture documentation, API details, or codebase references, please see [`REFERENCE.md`](../REFERENCE.md) in the main repository.

## Table of Contents

### 1. Introduction & Setup
* [Getting Started](getting-started.md) — Installation, your first login, and running your first AI-assisted incident session.

### 2. Administrator Guide
* [Administrator Guide](admin-guide.md) — User authentication, runtime configurations, LLM providers, setting up MCP servers, chat bot connectors, webhooks, and ingest tokens.
* [Integrations Guide](integrations-guide.md) — Incident ingest adapters, chat bot connectors, outbound webhooks, and Docker deployment basics.
* [Skills Guide](skills-guide.md) — Managing MCP servers, enforcement tiers, and capability examples.

### 3. Operator Guide
* [Operator Guide](operator-guide.md) — Incident triage flow, managing approvals, interacting with session chat, using the audit log, and understanding rollback behavior.
* [Paging Guide](paging-guide.md) — Top-level orientation: the incident-response loop, services / teams / rosters / chains / priority rules / response modes / maintenance windows / notification preferences, a setup walkthrough, and an end-to-end verification recipe.
* [Notification Preferences](notification-preferences.md) — Channels, per-priority routing matrix, quiet hours, dedup, and maintenance windows.
* [Slack as your paging surface](slack-paging-surface.md) — Block Kit page cards, slash commands (`/ack` / `/take` / `/release` / `/resolve` / `/snooze` / `/status`), per-incident channel mirroring, and Slack app setup.
* [Teams as your paging surface](teams-paging-surface.md) — Adaptive-card page cards with Acknowledge / Take Over / Resolve actions via the Microsoft Bot Framework, Graph app-only OAuth setup, and a verification recipe.
* [Responding from your phone](mobile-incident-response.md) — Which OpsMender pages are mobile-optimized, the chat → web UI flow on phones, and a real-device verification checklist.
* [Troubleshooting](troubleshooting.md) — Solutions for common login, connectivity, and configuration issues.
