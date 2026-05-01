# Getting Started with AIM

Welcome to the AI Incident Manager (AIM). This guide will walk you through spinning up AIM locally, logging in for the first time, ingesting your first incident, and starting your first AI-assisted session.

## 1. Installation & Running Locally

AIM is composed of a FastAPI backend, a Next.js frontend, and a PostgreSQL database. The easiest way to run the entire stack locally is via Docker Compose.

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
   - Backend API: `http://localhost:8000/docs`
   - Frontend Dashboard: `http://localhost:3000`

## 2. Your First Login

1. Open your browser and navigate to `http://localhost:3000`.
2. You will be redirected to the login page.
3. For local development, AIM includes a default admin account. Use the following credentials:
   - **Username:** `admin`
   - **Password:** `admin`
4. Upon successful login, you will land on the **Incidents Dashboard**, which will initially be empty.

## 3. Creating Your First Incident

Incidents can be ingested automatically via webhooks (e.g., LegacyAlertVendor, Datadog) using Ingest Tokens, but you can also create them manually.

1. On the Incidents dashboard, click **New Incident**.
2. Fill out the core details:
   - **Title:** "High Latency on API Gateway"
   - **Description:** "The API gateway is experiencing elevated p99 latency spikes."
   - **Severity:** Select `High`.
3. Click **Save**. The incident will now appear in your active incidents list.

## 4. Running Your First Session

AIM's primary superpower is its AI-assisted incident response sessions.

1. Click on the incident you just created to open its details panel.
2. Click **Start Session**. This spins up an autonomous AI agent dedicated to triaging, investigating, and resolving this specific incident.
3. You will be taken to the **Session Chat** interface.
4. The AI will introduce itself and may automatically begin investigating based on the incident description. You can guide the AI by sending messages in the chat:
   - *"Check the recent logs for the API Gateway."*
   - *"Do we have any active maintenance windows?"*
5. The AI will use its connected MCP (Model Context Protocol) tools to query metrics, logs, and perform actions.

Congratulations! You've successfully deployed AIM and started your first AI-assisted incident response session.

Next, check out the [Administrator Guide](admin-guide.md) to configure LLM providers and set up integrations.
