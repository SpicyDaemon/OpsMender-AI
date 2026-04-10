# AI Incident Manager (AIM)

An AI-powered incident response framework with tiered access controls. Connects AI agents to infrastructure via MCP servers and enforces a tier-based permission system that organizations define themselves.

## Quick Start

```bash
uv sync --dev
uv run aim --version
uv run aim check
```

## Running Tests

```bash
uv run pytest              # all tests
uv run pytest -xvs         # verbose, stop on first failure
uv run pytest tests/test_workflow.py  # single test file
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `aim` | Load config and print it |
| `aim --version` | Show version |
| `aim check` | Validate config and test MCP server connectivity |
| `aim audit` | View the audit log (human-readable table) |
| `aim audit --last N` | Show the last N audit entries |
| `aim audit --session ID` | Filter audit entries by session ID |
| `aim audit --json` | Output audit entries as raw JSONL |

## Configuration

Edit `config.yaml` to add MCP servers. Three transport types are supported:

```yaml
mcp_servers:
  # Local process (stdio)
  - name: kubernetes
    transport: stdio
    command: "npx"
    args: ["-y", "@anthropic/mcp-server-k8s"]

  # Server-Sent Events (sse)
  - name: remote-k8s
    transport: sse
    url: "http://mcp.internal:8080/sse"

  # Streamable HTTP (Sourcebot, etc.)
  - name: sourcebot
    transport: http
    url: "https://sb.example.com/api/mcp"
    token: "your-bearer-token"

audit:
  output: ./logs/audit.jsonl
```

## Workflow

AIM uses a LangGraph-powered incident response workflow:

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

Organizations define what's safe, cautious, or destructive in a `SKILL.md` file. See `examples/SKILL.md` for a Kubernetes reference template.

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

## Tier System

| Tier | safe | caution | destructive |
|------|------|---------|-------------|
| 0 | permit | permit | permit (sandbox only) |
| 1 | permit | permit | permit (requires approval) |
| 2 | permit | permit | deny |
| 3 | advise-only | deny | deny |

## Project Structure

```
ai-incident-manager/
├── backend/
│   ├── agent/              # LangGraph workflow, nodes, state, LLM interface
│   ├── audit/              # JSONL audit logger + audited tool executor
│   ├── config_loader.py    # YAML config → typed dataclasses
│   ├── mcp/                # MCP client wrapper (stdio, sse, http)
│   ├── skills/             # Skill definition parser (SKILL.md)
│   └── tiers/              # Tier enforcement layer
├── cli/
│   └── aim.py              # CLI entry point (check, audit)
├── examples/
│   └── SKILL.md            # Reference Kubernetes skill definition
├── tests/                  # 148 tests
├── config.yaml             # Default configuration
└── docs/                   # Project documentation
```

See `docs/REFERENCE.md` for full architecture details.
