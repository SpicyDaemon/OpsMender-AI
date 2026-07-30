// Feature: v1.1 headline acceptance. Creates a service with zero MCP servers
// and one native integration, starts sessions, and records browser evidence.

import fs from "node:fs";
import path from "node:path";

import { config, qaName, qaSlug } from "../lib/config.mjs";

function headers(h) {
  return {
    Authorization: `Bearer ${h.auth.token}`,
    ...(h.auth.orgId ? { "X-Org-ID": h.auth.orgId } : {}),
  };
}

async function requestJson(h, method, route, data) {
  const response = await h.request.fetch(`${config.baseUrl}${route}`, {
    method,
    headers: headers(h),
    ...(data === undefined ? {} : { data }),
  });
  if (!response.ok()) {
    throw new Error(
      `${method} ${route} failed: ${response.status()} ${await response.text()}`,
    );
  }
  return response.status() === 204 ? null : response.json();
}

export default {
  id: "mcp_optional",
  title: "v1.1 — MCP-optional service acceptance",
  async run(h) {
    const connectorName = qaName("native-github");
    const serviceName = qaName("integration-only");

    await h.step("create integration-only service with zero MCP servers", async () => {
      const teams = await requestJson(h, "GET", "/teams");
      const team =
        teams.items.find((item) => item.name === h.state.teamName) ?? teams.items[0];
      if (!team) throw new Error("no team is available for the acceptance service");

      const connector = await requestJson(h, "POST", "/integrations", {
        kind: "github",
        name: connectorName,
        base_url: "https://api.github.com",
        auth_type: "pat",
        auth: { token: "qa-not-used" },
        config: {},
        is_enabled: true,
      });
      h.state.mcpOptionalConnectorId = connector.id;

      const template = await requestJson(h, "GET", "/skills/template?template=blank");
      await requestJson(h, "POST", "/skills", {
        name: qaName("integration-policy"),
        description: "QA policy for the MCP-optional v1.1 acceptance path.",
        content_md: template.content_md,
        assignment: "integration",
        integration_connector_id: connector.id,
      });

      const service = await requestJson(h, "POST", "/services", {
        team_id: team.id,
        name: serviceName,
        slug: qaSlug("integration-only"),
        description: "Zero MCP servers; native integration tools only.",
        priority: "P2",
        mcp_server_ids: [],
        allowed_integration_connector_ids: [connector.id],
        ai_default_tier: 1,
        is_active: true,
      });
      h.state.mcpOptionalServiceId = service.id;
      if (service.mcp_server_ids.length !== 0) {
        throw new Error("acceptance service unexpectedly has MCP servers");
      }
      if (!service.allowed_integration_connector_ids.includes(connector.id)) {
        throw new Error("acceptance connector is missing from the service allowlist");
      }
    });

    await h.step("fire incident and start a Tier 1 session", async () => {
      const fired = await requestJson(h, "POST", "/incidents/fire-test", {
        service_id: h.state.mcpOptionalServiceId,
      });
      h.state.mcpOptionalIncidentId = fired.incident.id;
      await requestJson(
        h,
        "POST",
        `/incidents/${fired.incident.id}/ack`,
        { via: "api" },
      );
      const session = await requestJson(h, "POST", "/sessions", {
        incident_id: fired.incident.id,
        tier: 1,
      });
      h.state.mcpOptionalSessionId = session.id;
      if (session.tier !== 1) {
        throw new Error(`expected Tier 1 session, received Tier ${session.tier}`);
      }
    });

    await h.step("integration tools are offered and setup is complete without MCP", async () => {
      const discovered = await requestJson(h, "POST", "/skills/discover", {
        integration_connector_id: h.state.mcpOptionalConnectorId,
      });
      const names = discovered.tools.map((tool) => tool.name);
      if (!names.some((name) => name.includes("__create_issue__"))) {
        throw new Error("mutating GitHub integration capability was not offered");
      }
      const checklist = await requestJson(h, "GET", "/config/setup-checklist");
      if (checklist.mcp_server_added) {
        throw new Error("fresh acceptance workspace unexpectedly has an MCP server");
      }
      if (!checklist.integration_connected || !checklist.all_complete) {
        throw new Error(
          `integration checklist path incomplete: ${JSON.stringify(checklist)}`,
        );
      }
    });

    await h.step("the same incident can continue in Tier 2 advisory mode", async () => {
      const session = await requestJson(h, "POST", "/sessions", {
        incident_id: h.state.mcpOptionalIncidentId,
        tier: 2,
      });
      if (session.id !== h.state.mcpOptionalSessionId || session.tier !== 2) {
        throw new Error("session takeover did not preserve identity at Tier 2");
      }
    });

    await h.step("capture integration-covered service evidence", async () => {
      await h.page.goto("/dashboard/paging/services", {
        waitUntil: "domcontentloaded",
        timeout: 60000,
      });
      await h.page.getByPlaceholder(/search services/i).fill(serviceName);
      const row = h.page.locator("tr").filter({ hasText: serviceName }).first();
      await row.waitFor({ state: "visible" });
      await row
        .getByText(/Integrations are covering this service's toolset/i)
        .waitFor({ state: "visible" });
      const screenshot = path.join(
        config.reportDir,
        "mcp-optional-acceptance.png",
      );
      fs.mkdirSync(config.reportDir, { recursive: true });
      await h.page.screenshot({ path: screenshot, fullPage: true });
      h.state.mcpOptionalScreenshot = screenshot;
    });
  },
};
