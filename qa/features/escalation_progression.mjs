// Live browser proof for level deletion, progression, exhaustion and warnings.

import assert from "node:assert/strict";
import { config, qaName, qaSlug } from "../lib/config.mjs";
import { Harness } from "../lib/harness.mjs";

async function api(h, method, route, data) {
  const response = await h.request[method](`${config.baseUrl}${route}`, {
    headers: { Authorization: `Bearer ${h.auth.token}` },
    ...(data ? { data } : {}),
  });
  const body = await response.json().catch(() => null);
  assert.ok(response.ok(), `${method.toUpperCase()} ${route}: ${response.status()} ${JSON.stringify(body)}`);
  return body;
}

async function chainState(h, id) {
  return api(h, "get", `/incidents/${id}/chain`);
}

export default {
  id: "escalation_progression",
  title: "Paging — escalation progression",
  async run(h) {
    if (!config.livePaging) {
      await h.step("escalation progression checks", async () => {
        throw Harness.skip("QA_LIVE_PAGING not enabled");
      });
      return;
    }

    const state = {};
    await h.step("prepare a three-level chain and an eligible paging service", async () => {
      state.me = await api(h, "get", "/auth/me");
      state.team = await api(h, "post", "/teams", {
        name: qaName("progress-team"), slug: qaSlug("progress-team"),
      });
      await api(h, "post", `/teams/${state.team.id}/members`, {
        user_id: state.me.id, role: "member",
      });
      state.chain = await api(h, "post", "/escalation-chains", {
        name: qaName("progress-chain"), team_id: state.team.id,
      });
      state.steps = [];
      for (let index = 0; index < 3; index += 1) {
        state.steps.push(await api(h, "post", `/escalation-chains/${state.chain.id}/steps`, {
          step_index: index, target_type: "user", target_id: state.me.id,
          timeout_seconds: 20,
        }));
      }
      state.service = await api(h, "post", "/services", {
        name: qaName("progress-service"), slug: qaSlug("progress-service"),
        team_id: state.team.id, priority: "P1",
      });
      await api(h, "post", `/services/${state.service.id}/escalation-chains`, {
        chain_id: state.chain.id,
      });
    });

    await h.step("delete middle level in the editor and show two remaining levels", async () => {
      await h.goto("/dashboard/paging/escalation-chains");
      const row = h.page.getByRole("row").filter({ hasText: state.chain.name }).first();
      await row.getByRole("button", { name: "Expand row details" }).click();
      const levels = h.page.locator("li[draggable]:visible");
      await levels.first().waitFor({ state: "visible" });
      assert.equal(await levels.count(), 3);
      h.page.once("dialog", (dialog) => dialog.accept());
      const [deleted] = await Promise.all([
        h.page.waitForResponse((response) =>
          response.url().includes(`/escalation-chains/${state.chain.id}/steps/${state.steps[1].id}`) &&
          response.request().method() === "DELETE"),
        levels.nth(1).getByTitle("Delete step").click(),
      ]);
      assert.equal(deleted.status(), 204);
      await h.page.waitForFunction(() =>
        [...document.querySelectorAll("li[draggable]")].filter((item) => item.getBoundingClientRect().height > 0).length === 2);
      const saved = await api(h, "get", `/escalation-chains/${state.chain.id}/steps`);
      assert.deepEqual(saved.items.map((item) => item.step_index), [0, 1]);
      assert.deepEqual(saved.items.map((item) => item.id), [state.steps[0].id, state.steps[2].id]);
    });

    await h.step("incident visibly progresses through the remaining level and exhausts", async () => {
      state.incident = await api(h, "post", "/incidents", {
        title: qaName("progress-incident"), description: "Part 4 browser proof",
        severity: "high", service_id: state.service.id,
      });
      await h.goto(`/dashboard/incidents/detail?id=${state.incident.id}`);
      await h.expectText(state.incident.title);
      assert.deepEqual((await chainState(h, state.incident.id)).pages
        .filter((page) => page.channel === "recorded").map((page) => page.step_index), [0]);
      const deadline = Date.now() + 70000;
      let panel;
      while (Date.now() < deadline) {
        panel = await chainState(h, state.incident.id);
        if (panel.state?.status === "exhausted") break;
        await h.page.waitForTimeout(2000);
      }
      assert.equal(panel?.state?.status, "exhausted");
      assert.deepEqual(panel.pages.filter((page) => page.channel === "recorded")
        .map((page) => page.step_index).sort(), [0, 1]);
      await h.page.reload();
      await h.expectText(/Escalation exhausted/i);
      const inbox = await api(h, "get", "/notifications");
      assert.ok(JSON.stringify(inbox).includes("incident.escalation_exhausted"));
    });

    await h.step("service form warns when no chain matches P1", async () => {
      state.unmatched = await api(h, "post", "/services", {
        name: qaName("unmatched-service"), slug: qaSlug("unmatched-service"),
        team_id: state.team.id, priority: "P1",
      });
      await h.goto("/dashboard/paging/services");
      const row = h.page.getByRole("row").filter({ hasText: state.unmatched.name }).first();
      await row.getByTitle("Edit service").click();
      await h.page.getByRole("alert").filter({ hasText: "No escalation chain matches" }).waitFor({ state: "visible" });
    });

    await h.step("roster form rejects an invalid time", async () => {
      await h.goto("/dashboard/paging/rosters");
      await h.page.getByRole("button", { name: /new roster/i }).first().click();
      await h.page.getByPlaceholder("Primary on-call").fill(qaName("invalid-roster"));
      await h.page.getByPlaceholder("18:00").fill("24:00");
      const [rejected] = await Promise.all([
        h.page.waitForResponse((response) =>
          response.url().endsWith("/rosters") && response.request().method() === "POST"),
        h.page.getByRole("button", { name: /^create$/i }).click(),
      ]);
      assert.equal(rejected.status(), 422);
      await h.page.waitForTimeout(300);
    });
  },
};
