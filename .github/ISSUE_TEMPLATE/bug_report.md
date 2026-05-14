---
name: Bug report
about: Something isn't working as documented
title: "[bug] "
labels: ["bug", "triage"]
assignees: []
---

## Summary

<!-- A clear, one-paragraph description of the bug. -->

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What did you expect to happen? -->

## Actual behavior

<!-- What actually happened? Include error text / stack traces in a code block. -->

```
(paste logs, stack trace, or error output here)
```

## Environment

- OpsMender version: <!-- `opsmender --version`, Docker tag, or commit SHA -->
- Install method: <!-- docker / pyinstaller binary / `uv sync` from source -->
- OS: <!-- e.g. macOS 14.4, Ubuntu 22.04, Windows 11 -->
- Python version (source installs only): <!-- `python --version` -->
- Browser (UI bugs only): <!-- e.g. Chrome 124, Firefox 125, Safari 17 -->
- LLM provider: <!-- anthropic / openai / azure_openai / ollama -->
- MCP server(s) connected: <!-- name + transport (stdio/sse/http) -->

## Configuration (redacted)

<!--
Paste the relevant parts of your .env or runtime config. Redact any secrets:
API keys, tokens, DB passwords, webhook URLs containing secrets.
-->

```
OPSMENDER_TIER=...
OPSMENDER_DATABASE_URL=postgres://***:***@host/db
```

## Anything else?

<!-- Audit log snippets, UI screenshots, or additional context. -->
