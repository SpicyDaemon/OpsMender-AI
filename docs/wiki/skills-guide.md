# Skills and tool sources

OpsMender Skills are `SKILL.md` policies that tell the AI how to use a tool
source at each AI Autonomy Tier. Tool sources can be:

- an **MCP server** using stdio, HTTP, or SSE; or
- an encrypted **native integration connector** such as Kubernetes, GitHub,
  Jira, or Terraform Cloud.

A Service may use MCP, native integrations, or both. If neither source provides
tools, its AI session still starts in advisory-only mode.

## Managing Skills

Open **MCP Skills** at `/dashboard/skills`. MCP Skill Studio supports:

- **New skill** — start with Blank, Kubernetes, Cloud infrastructure, CI/CD &
  source control, or Ticketing & communications policy templates.
- **Generate from tools** — discover operations from an MCP server or native
  integration connector, review conservative policy suggestions, and generate
  a draft.
- **Import, edit, clone, and download** — manage existing Markdown policies.
- **Backend validation** — line-specific parser errors block save; warnings and
  the parsed operation table remain visible for review.
- **Policy diff and coverage** — generated changes highlight risk or permission
  widening, and bound sources list tools with no matching operation. An
  unclassified tool is denied at every tier.

## Assignment

A Skill may be:

- **Unassigned** — saved and downloadable, but never used by a session.
- **Global fallback** — applies to MCP servers without a server-specific Skill.
- **MCP server** — governs that server's exact discovered tools.
- **Integration connector** — adds connector-specific instructions and may
  restrict its capability policy.

An integration-bound Skill cannot relax the connector's built-in capability
baseline. The more restrictive policy wins, mutating operations keep their
approval/advisory floor, and malformed policy fails closed.

## AI Autonomy Tiers

| Tier | Behavior |
|---|---|
| **Tier 0 — Autonomous** | Runs only explicitly permitted operations within deny lists, generic-command restrictions, and the reversible-operation floor. |
| **Tier 1 — Approval Required** | Each explicit operation policy chooses autonomous execution, operator approval, advisory behavior, or blocking. |
| **Tier 2 — Advisory Only** | Read-only observation and recommendations; no write or remediation action executes. |

The backend tier gate is authoritative. Skill prose can guide investigation
order, evidence requirements, and rollback expectations, but it cannot change
the selected tier, bypass approval, override a deny, or make an unavailable tool
appear.

## Connecting MCP servers

Admins add MCP servers under `/dashboard/mcp-servers`:

1. **Stdio** runs a local command such as `npx
   @modelcontextprotocol/server-postgres ...`.
2. **HTTP / SSE** connects to a reachable service URL. Supported URL-based
   servers can use OAuth 2.1 + PKCE through **Connect**.

Use an MCP-server-specific Skill when its operation identifiers or environment
rules differ from the global fallback.

## Connecting native integrations

Admins add connectors under `/dashboard/integrations`. Active connector
capabilities become internal tools for the incident session without requiring
an MCP server. Bind a Skill to the connector when you need source-specific
instructions or tighter operation policy.

For the full policy syntax, generic-command guardrail, template fields, and
generator walkthrough, see [MCP Skills & AI Autonomy Tiers](mcp-skills.md).
