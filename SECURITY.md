# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `1.x`   | Yes       |
| `< 1.0` | No        |

Only the latest `1.x` minor release receives security patches. Older versions will not be backported.

## Reporting a vulnerability

Please do **not** file security issues as public GitHub issues.

Use GitHub's [private vulnerability reporting](https://github.com/SpicyDaemon/OpsMender-AI/security/advisories/new) to disclose vulnerabilities privately. Include a short description in the advisory title.

Include:

- A description of the issue and the impact you believe it has
- Steps to reproduce (minimal PoC preferred)
- The version / commit of OpsMender you tested against
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

- The `opsmender` CLI and Python backend (FastAPI, LangGraph workflow, MCP client, tier gate, audit log)
- The Next.js dashboard
- The Docker image and PyInstaller binary published from this repository
- Ingest / outbound webhook handling

Out of scope:

- Vulnerabilities in third-party MCP servers an operator chooses to connect
- Vulnerabilities in the LLM providers (Anthropic, OpenAI, Azure OpenAI, Ollama)
- Social engineering of maintainers or users
- DoS via obviously malicious load patterns against a self-hosted instance

## Hardening expectations

OpsMender is meant to be deployed inside an organization's trusted network. The threat model assumes:

- Administrators are trusted.
- Operators and viewers are authenticated and authorized via the built-in JWT + role model.
- The tier gate is the final guard against destructive actions — it is programmatic and cannot be bypassed by agent reasoning. Reports showing a way to bypass the tier gate are treated as critical.

If you have questions about whether something is in scope, open an issue first — it's fine to ask.

## Supply-chain verification

- Generate a CycloneDX SBOM with `bash scripts/generate-sbom.sh`.
- Scan a locally built image with
  `bash scripts/scan-image.sh <image-reference>`.
- Sign an immutable image digest with
  `COSIGN_KEY=/path/to/key bash scripts/sign-image.sh <image>@sha256:<digest>`.

The repository also runs CodeQL and Trivy on `main` and uses Dependabot for
weekly dependency-update proposals. These controls support security review;
they are not a compliance certification.
