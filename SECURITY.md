# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `1.x`   | Yes       |
| `< 1.0` | No        |

Only the latest `1.x` minor release receives security patches. Older versions will not be backported.

## Reporting a vulnerability

Please do **not** file security issues as public GitHub issues.

Report vulnerabilities privately by emailing **noreply@opsmender.local** with the subject line `AIM SECURITY: <short description>`.

Include:

- A description of the issue and the impact you believe it has
- Steps to reproduce (minimal PoC preferred)
- The version / commit of AIM you tested against
- Any suggested remediation if you have one

## What to expect

- **Acknowledgement** within 5 business days of your report.
- **Triage** within 10 business days — I will confirm whether I can reproduce and give an initial severity assessment.
- **Fix timeline** depends on severity:
  - Critical (remote code execution, auth bypass, credential exposure): patched as quickly as possible, typically within 7 days.
  - High (privilege escalation, data leakage with auth): patched in the next minor release.
  - Medium / low: scheduled into the normal release cycle.
- A security advisory will be published on the GitHub Security tab when a fix ships, with credit to the reporter (unless you ask to stay anonymous).

## Scope

In scope:

- The `aim` CLI and Python backend (FastAPI, LangGraph workflow, MCP client, tier gate, audit log)
- The Next.js dashboard
- The Docker image and PyInstaller binary published from this repository
- Ingest / outbound webhook handling

Out of scope:

- Vulnerabilities in third-party MCP servers an operator chooses to connect
- Vulnerabilities in the LLM providers (Anthropic, OpenAI, Azure OpenAI, Ollama)
- Social engineering of maintainers or users
- DoS via obviously malicious load patterns against a self-hosted instance

## Hardening expectations

AIM is meant to be deployed inside an organization's trusted network. The threat model assumes:

- Administrators are trusted.
- Operators and viewers are authenticated and authorized via the built-in JWT + role model.
- The tier gate is the final guard against destructive actions — it is programmatic and cannot be bypassed by agent reasoning. Reports showing a way to bypass the tier gate are treated as critical.

If you have questions about whether something is in scope, email first — it's fine to ask.
