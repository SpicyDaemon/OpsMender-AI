# Getting Started with OpsMender

This guide walks you through running OpsMender locally, signing in for the
first time, creating your first incident, and starting your first AI session.
OpsMender is one self-hosted workspace per instance: a FastAPI backend that
also serves the dashboard, and a PostgreSQL database.

## 1. Run OpsMender locally

**Prerequisites:**
- Docker with Compose
- Git

**Steps:**
1. Clone the repository:
   ```bash
   git clone https://github.com/SpicyDaemon/OpsMender-AI.git
   cd OpsMender-AI
   ```
2. Copy the development environment file; its defaults work as they are:
   ```bash
   cp .env.example .env
   ```
3. Start the stack:
   ```bash
   docker compose -f docker/docker-compose.yml up --build
   ```
4. Verify it is up:
   - Readiness: `http://localhost:8000/health/ready` answers 200.
   - Dashboard: `http://localhost:8000`.
   - Interactive API docs: `http://localhost:8000/docs` (development only;
     in production they stay off unless `OPSMENDER_ENABLE_API_DOCS=true`).

To stop, run `docker compose -f docker/docker-compose.yml down`; add `-v` to
wipe the database as well.

## 2. Your first sign-in

1. Open `http://localhost:8000`. You land on the login page.
2. The development stack seeds an admin account, `admin` / `admin123`. It is
   for development only. For a production install, set
   `OPSMENDER_BOOTSTRAP_ADMIN_EMAIL` and `OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD`
   before the first start instead; the README's production setup shows the
   full environment.
3. Invite everyone else from **People**: Admin, Operator or Viewer. Open
   self-registration closes once a user exists. The [Auth Guide](auth-guide.md)
   covers the default model and the [Advanced Auth Guide](advanced-auth-guide.md)
   covers OIDC and SAML.
4. After signing in you land on the **Incidents** dashboard, empty at first.

## 3. Create your first incident

Incidents arrive on their own through a service's alert intake URL, which
monitors such as Datadog, CloudWatch or any JSON sender can POST to (see the
[Administrator Guide](admin-guide.md), section 9). You can also create one by
hand:

1. On **Incidents**, click **New Incident**.
2. Fill in a title such as "High latency on API gateway", a description, and a
   severity.
3. Save. The incident appears in the active list.

## 4. Run your first AI session

A session needs a model. Before the first one, open **Models** and save a
provider configuration (the [Administrator Guide](admin-guide.md), section 3,
walks through it). Tools come from the MCP servers and native integration
connectors allowlisted on the incident's Service; a session without either
source still runs and says plainly that it cannot act.

1. Open the incident and acknowledge it. A Tier 1 or Tier 2 session can only
   start on an incident someone holds.
2. Click **Start Session**. The dialog opens on **Tier 1 (Approval Required)**:
   the session reads on its own and asks an operator before any write.
   **Tier 2 (Advisory Only)** lets it read and advise without writing, and
   **Tier 0 (Autonomous)** lets it act within the Skill's policy. Whatever the
   model proposes, the tier gate in the backend decides what runs.
3. Follow the session page: the event stream and tool activity show every
   call and its outcome, the Co-pilot panel takes your messages, and the model
   summary at the end is held to the stored record (the
   [Operator Guide](operator-guide.md) explains the grounding).

You have deployed OpsMender and run your first AI-assisted session.

Next, read the [Administrator Guide](admin-guide.md) to configure model
providers and connect infrastructure through
[MCP servers and Skills](mcp-skills.md), [native integrations](integrations-guide.md),
or both, and the [Paging Guide](paging-guide.md) to set up services, rosters
and escalation. The setup checklist marks infrastructure complete when either
tool source has an active connection.
