# Getting Started with OpsMender

Welcome to the OpsMender AI (OpsMender). This guide will walk you through spinning up OpsMender locally, logging in for the first time, ingesting your first incident, and starting your first AI-assisted session.

## 1. Installation & Running Locally

OpsMender is composed of a FastAPI backend, a Next.js frontend, and a PostgreSQL database. The easiest way to run the entire stack locally is via Docker Compose.

**Prerequisites:**
- Docker and Docker Compose installed
- Git

**Steps:**
1. Clone the repository:
   ```bash
   git clone https://github.com/SpicyDaemon/OpsMender-AI.git
   cd OpsMender-AI
   ```
2. Start the stack:
   ```bash
   docker-compose up -d
   ```
3. Verify the services are running:
   - Backend API: `http://localhost:8000/health`
   - Interactive API docs: `http://localhost:8000/docs` (development only —
     disabled in production unless `OPSMENDER_ENABLE_API_DOCS=true`)
   - Frontend Dashboard: `http://localhost:3000`

## 2. Your First Login

1. Open your browser and navigate to `http://localhost:3000`.
2. You will be redirected to the login page.
3. For first-run setup, click **Register** to create your admin account. The first registered user is automatically assigned the `admin` role and linked to a default "Main" organization.
4. Upon successful login, you will land on the **Incidents Dashboard**, which will initially be empty and scoped to your organization.

## 3. Creating Your First Incident

Incidents can be ingested automatically via service alert intake webhooks (e.g., Datadog, CloudWatch, or generic JSON senders), but you can also create them manually.

1. On the Incidents dashboard, click **New Incident**.
2. Fill out the core details:
   - **Title:** "High Latency on API Gateway"
   - **Description:** "The API gateway is experiencing elevated p99 latency spikes."
   - **Severity:** Select `High`.
3. Click **Save**. The incident will now appear in your active incidents list.

## 4. Running Your First Session

OpsMender's primary superpower is its AI-assisted incident response sessions.

1. Click on the incident you just created to open its details panel.
2. Click **Start Session**. This spins up an autonomous AI agent dedicated to triaging, investigating, and resolving this specific incident.
3. You will be taken to the **Session Chat** interface.
4. The AI will introduce itself and may automatically begin investigating based on the incident description. You can guide the AI by sending messages in the chat:
   - *"Check the recent logs for the API Gateway."*
   - *"Do we have any active maintenance windows?"*
5. The AI uses the tools assigned to the incident's Service. Those tools may
   come from MCP (Model Context Protocol) servers, native integration
   connectors, or both. If the Service has neither source, the session still
   starts in advisory-only mode and explains that no executable tools are
   available.

Congratulations! You've successfully deployed OpsMender and started your first AI-assisted incident response session.

Next, check out the [Administrator Guide](admin-guide.md) to configure LLM
providers and connect infrastructure through
[MCP servers and Skills](mcp-skills.md), [native integrations](integrations-guide.md),
or both. The setup checklist marks infrastructure complete when either source
has an active connection.
