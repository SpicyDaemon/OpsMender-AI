# Integrations Guide

AIM is built to sit at the center of your incident response ecosystem. It ingests alerts from your existing monitoring tools and broadcasts updates to your collaboration platforms.

## 1. Incident Ingest Adapters

AIM provides a unified `/incidents/ingest` webhook endpoint. To secure and route incoming alerts, you generate **Ingest Tokens**.

AIM natively supports several popular monitoring tools:
- **LegacyAlertVendor:** Parses LegacyAlertVendor webhooks to extract incident details and severity.
- **Datadog:** Parses Datadog monitor alerts.
- **AWS CloudWatch (SNS):** Parses CloudWatch ALARM and OK states sent via SNS.
- **Azure Monitor:** Parses the Common Alert Schema v2.
- **GCP Cloud Monitoring:** Parses incident webhook v1.2.
- **Oracle Cloud (OCI):** Parses CHRONOS_NOTIFICATION alarms.
- **LegacyAlertRelay:** Parses LegacyAlertRelay alert webhooks.

**Universal (Auto) Adapter:**
If your tool is not listed above, AIM provides an `auto` provider option. The Universal Adapter uses an LLM to dynamically inspect the incoming JSON payload, learn its structure, and extract the title, description, and severity automatically. It caches the structural mapping for performance on subsequent alerts.

## 2. Outbound Webhooks

AIM can push real-time updates about incident sessions, AI actions, and SLA/SLO violations to external platforms.

1. Navigate to **Config** > **Webhook Triggers**.
2. AIM supports formatted payloads for:
   - **Slack:** Sends beautifully formatted block-kit messages with incident details and links.
   - **Microsoft Teams:** Sends adaptive cards.
   - **Sumo Logic:** Sends structured JSON for ingestion into log analytics.
   - **Generic:** Sends a standard JSON payload containing the event data.

## 3. Docker Deployment Basics

If you are deploying AIM in a production environment, use the provided Dockerfiles.

- The repository includes a `docker-compose.yml` that orchestrates the backend (`fastapi`), frontend (`nextjs`), and the PostgreSQL database.
- **Environment Variables:** Ensure you map `DATABASE_URL` and your encryption keys (`AIM_SECRET_KEY`) securely.
- **Networking:** The MCP servers can be run as sidecar containers or standalone services, provided the AIM backend container has network access to them.
