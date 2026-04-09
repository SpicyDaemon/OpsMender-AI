# AI Incident Manager (AIM)

An AI-powered incident response framework with tiered access controls. Connects AI agents to infrastructure via MCP servers and enforces a tier-based permission system that organizations define themselves.

## Quick Start

```bash
uv sync --dev
.venv/bin/aim --version
.venv/bin/aim check
```

## Running Tests

```bash
.venv/bin/pytest tests/ -v
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `aim` | Load config and print it |
| `aim --version` | Show version |
| `aim check` | Validate config and test MCP server connectivity |

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
```

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
│   ├── config_loader.py   # YAML config -> typed dataclasses
│   ├── mcp/               # MCP client wrapper (stdio, sse, http)
│   ├── skills/            # Skill definition parser (SKILL.md)
│   └── tiers/             # Tier enforcement layer
├── cli/
│   └── aim.py             # CLI entry point
├── examples/
│   └── SKILL.md           # Reference Kubernetes skill definition
├── tests/                 # 50 tests
├── config.yaml            # Default configuration
└── docs/                  # Project documentation
```

See `docs/REFERENCE.md` for full architecture details.
