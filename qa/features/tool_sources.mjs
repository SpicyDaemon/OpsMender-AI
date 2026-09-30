// Phase 5 acceptance for a service with no tool source and an overlapping pair.
// Sessions are opt-in because starting one may contact a configured model.

import { Harness } from "../lib/harness.mjs";
import { config, qaName, qaSlug } from "../lib/config.mjs";

async function requestJson(h, method, route, data) {
  const response = await h.request.fetch(`${config.baseUrl}${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${h.auth.token}`,
      ...(h.auth.orgId ? { "X-Org-ID": h.auth.orgId } : {}),
    },
    ...(data === undefined ? {} : { data }),
  });
  if (!response.ok()) {
    throw new Error(`${method} ${route} failed with ${response.status()}`);
  }
  return response.json();
}

async function serviceRow(h, name) {
  await h.goto("/dashboard/paging/services");
  await h.page.getByPlaceholder(/search services/i).fill(name);
  const row = h.page.locator("tr").filter({ hasText: name }).first();
  await row.waitFor({ state: "visible" });
  return row;
}

export default {
  id: "tool_sources",
  title: "AI: tool source boundaries",
  async run(h) {
    const noSourceName = qaName("no-tool-source");
    const overlapName = qaName("overlapping-tool-sources");
    const singleSourceName = qaName("single-tool-source");

    await h.step("a service with no tool source is explicitly advisory only", async () => {
      const teams = await requestJson(h, "GET", "/teams");
      const team = teams.items.find((item) => item.name === h.state.teamName) ?? teams.items[0];
      if (!team) throw new Error("no team available for tool-source acceptance");
      h.state.toolSourceTeamId = team.id;
      const service = await requestJson(h, "POST", "/services", {
        team_id: team.id,
        name: noSourceName,
        slug: qaSlug("no-tool-source"),
        priority: "P2",
        mcp_server_ids: [],
        allowed_integration_connector_ids: [],
        ai_default_tier: 2,
        is_active: true,
      });
      h.state.noToolSourceServiceId = service.id;
      if (service.mcp_server_ids.length || service.allowed_integration_connector_ids.length) {
        throw new Error("the no-source service unexpectedly has a tool source");
      }
      const row = await serviceRow(h, noSourceName);
      await row.getByText("Advisory only", { exact: true }).waitFor();
      if (await row.getByText("Overlapping tool sources").count()) {
        throw new Error("a no-source service received an overlap warning");
      }
    });

    await h.step("an overlapping service warns while a single-source service does not", async () => {
      const githubServer = await requestJson(h, "POST", "/mcp-servers", {
        name: qaName("github-mcp"),
        transport: "http",
        url: "https://example.invalid/mcp",
        is_active: true,
      });
      const connector = await requestJson(h, "POST", "/integrations", {
        kind: "github",
        name: qaName("overlap-connector"),
        base_url: "https://api.github.com",
        auth_type: "pat",
        auth: { token: "qa-not-used" },
        config: {},
        is_enabled: true,
      });
      const service = await requestJson(h, "POST", "/services", {
        team_id: h.state.toolSourceTeamId,
        name: overlapName,
        slug: qaSlug("overlapping-tool-sources"),
        priority: "P2",
        mcp_server_ids: [githubServer.id],
        allowed_integration_connector_ids: [connector.id],
        ai_default_tier: 2,
        is_active: true,
      });
      h.state.overlapServiceId = service.id;
      if (service.tool_source_overlaps.length !== 1) {
        throw new Error(`expected one overlapping pair, found ${service.tool_source_overlaps.length}`);
      }
      const single = await requestJson(h, "POST", "/services", {
        team_id: h.state.toolSourceTeamId,
        name: singleSourceName,
        slug: qaSlug("single-tool-source"),
        priority: "P2",
        mcp_server_ids: [],
        allowed_integration_connector_ids: [connector.id],
        ai_default_tier: 2,
        is_active: true,
      });
      if (single.tool_source_overlaps.length !== 0) {
        throw new Error("single-source service received an overlap warning");
      }
      const row = await serviceRow(h, overlapName);
      await row.getByText("Overlapping tool sources").waitFor();
      const note = row.getByText("Overlapping tool sources");
      const tooltip = await note.locator("xpath=..").getAttribute("title");
      if (!tooltip?.includes("only the native connector links tickets")) {
        throw new Error("overlap explanation is absent from the warning");
      }
      const control = await serviceRow(h, singleSourceName);
      await control.getByText(/Integrations are covering this service's toolset/i).waitFor();
      if (await control.getByText("Overlapping tool sources").count()) {
        throw new Error("single-source UI shows an overlap warning");
      }
    });

    await h.step("tool-source gaps and warnings do not block session start", async () => {
      if (!config.toolSourceSessions) {
        throw Harness.skip("QA_TOOL_SOURCE_SESSIONS is off; use an isolated offline-model demo instance");
      }
      if (!h.state.overlapServiceId) {
        throw new Error("overlap fixture was not created");
      }
      for (const serviceId of [h.state.noToolSourceServiceId, h.state.overlapServiceId]) {
        const fired = await requestJson(h, "POST", "/incidents/fire-test", { service_id: serviceId });
        await requestJson(h, "POST", `/incidents/${fired.incident.id}/ack`, { via: "api" });
        const session = await requestJson(h, "POST", "/sessions", {
          incident_id: fired.incident.id,
          tier: 2,
        });
        if (!session.id || session.tier !== 2) {
          throw new Error("the service did not start a Tier 2 session");
        }
        if (serviceId === h.state.noToolSourceServiceId) {
          let found = false;
          for (let attempt = 0; attempt < 20; attempt += 1) {
            const messages = await requestJson(h, "GET", `/sessions/${session.id}/messages`);
            found = messages.items.some((item) => item.content?.includes("session runs advisory-only"));
            if (found) break;
            await h.page.waitForTimeout(500);
          }
          if (!found) throw new Error("no advisory-only session explanation was recorded");
        }
      }
    });
  },
};
