// Browser proof for the acknowledgement lock: Take, comment, release,
// re-acknowledge and resolve on the incident page; bulk acknowledge and
// resolve from the incident list; and a takeover the owner must confirm.
// Guarded by QA_LIVE_PAGING because its Escalation Chains page real accounts
// (the QA user and an operator this feature creates). Disposable instance only.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import path from "node:path";
import { config, qaName, qaSlug } from "../lib/config.mjs";
import { Harness } from "../lib/harness.mjs";

const LEVEL_TIMEOUT_S = 20;
// A level timeout, one scheduler tick (10 s) and a margin.
const PAST_A_LEVEL_MS = (LEVEL_TIMEOUT_S + 20) * 1000;

async function api(request, method, route, { token, data, expect } = {}) {
  const response = await request[method](`${config.baseUrl}${route}`, {
    ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    ...(data ? { data } : {}),
  });
  const body = await response.json().catch(() => null);
  if (expect !== undefined) {
    assert.equal(response.status(), expect, `${method.toUpperCase()} ${route}: ${JSON.stringify(body)}`);
    return body;
  }
  if (!response.ok()) {
    throw new Error(`${method.toUpperCase()} ${route}: ${response.status()} ${JSON.stringify(body)}`);
  }
  return body;
}

async function chain(h, incidentId) {
  const panel = await api(h.request, "get", `/incidents/${incidentId}/chain`, { token: h.auth.token });
  return {
    status: panel.state?.status,
    step: panel.state?.current_step_index,
    lastActivity: panel.state?.last_activity_at,
    levels: panel.pages
      .filter((p) => p.channel === "recorded")
      .map((p) => p.step_index)
      .sort(),
  };
}

async function owner(h, incidentId) {
  const paging = await api(h.request, "get", `/incidents/${incidentId}/paging`, { token: h.auth.token });
  return paging.assignment && !paging.assignment.released_at ? paging.assignment.assigned_to : null;
}

async function capture(h, label) {
  await h.page.screenshot({
    path: path.join(config.reportDir, "screenshots", `${config.runId}-${label}.png`),
    fullPage: true,
  });
}

async function openIncident(h, incidentId, title) {
  await h.goto(`/dashboard/incidents/detail?id=${incidentId}`);
  await h.expectText(title);
}

function strip(h) {
  return h.page.getByTestId("incident-command-strip");
}

export default {
  id: "ownership_lifecycle",
  title: "Paging — ownership lifecycle",
  async run(h) {
    if (!config.livePaging) {
      await h.step("ownership lifecycle checks", async () => {
        throw Harness.skip("QA_LIVE_PAGING not enabled");
      });
      return;
    }
    const s = { incidents: [] };

    await h.step("prepare a two-level chain and a second operator", async () => {
      const token = h.auth.token;
      s.me = await api(h.request, "get", "/auth/me", { token });
      s.password = `Qa-${crypto.randomBytes(12).toString("hex")}`;
      s.otherName = qaSlug("operator");
      s.other = await api(h.request, "post", "/auth/users", {
        token,
        data: {
          username: s.otherName,
          email: `${s.otherName}@example.com`,
          role: "operator",
          password: s.password,
          require_password_change: false,
        },
      });
      const login = await api(h.request, "post", "/auth/login", {
        data: { username: s.otherName, password: s.password },
      });
      s.otherToken = login.access_token;
      const team = await api(h.request, "post", "/teams", {
        token,
        data: { name: qaName("lifecycle-team"), slug: qaSlug("lifecycle-team") },
      });
      const esc = await api(h.request, "post", "/escalation-chains", {
        token,
        data: { name: qaName("lifecycle-chain"), team_id: team.id },
      });
      for (const [index, userId] of [s.me.id, s.other.id].entries()) {
        await api(h.request, "post", `/escalation-chains/${esc.id}/steps`, {
          token,
          data: { step_index: index, target_type: "user", target_id: userId, timeout_seconds: LEVEL_TIMEOUT_S },
        });
      }
      s.service = await api(h.request, "post", "/services", {
        token,
        data: {
          name: qaName("lifecycle-service"),
          slug: qaSlug("lifecycle-service"),
          team_id: team.id,
          priority: "P1",
        },
      });
      await api(h.request, "post", `/services/${s.service.id}/escalation-chains`, {
        token,
        data: { chain_id: esc.id },
      });
    });

    const newIncident = async (label) => {
      const title = qaName(label);
      const incident = await api(h.request, "post", "/incidents", {
        token: h.auth.token,
        data: { title, description: "QA ownership lifecycle", severity: "high", service_id: s.service.id },
      });
      s.incidents.push(incident.id);
      const started = await chain(h, incident.id);
      assert.equal(started.status, "running");
      assert.deepEqual(started.levels, [0]);
      return { id: incident.id, title };
    };

    await h.step("Take stops paging for as long as the owner holds it", async () => {
      s.main = await newIncident("lifecycle-main");
      await openIncident(h, s.main.id, s.main.title);
      await strip(h).getByRole("button", { name: /^take$/i }).click();
      await strip(h).getByText("You own this").waitFor({ state: "visible" });
      assert.equal((await chain(h, s.main.id)).status, "acked");
      await h.page.waitForTimeout(PAST_A_LEVEL_MS);
      const held = await chain(h, s.main.id);
      assert.equal(held.status, "acked");
      assert.deepEqual(held.levels, [0], "level 2 must not be paged while the lock is held");
      await capture(h, "lifecycle-taken");
    });

    await h.step("a comment from the owner extends the lock", async () => {
      const before = (await chain(h, s.main.id)).lastActivity;
      const note = `QA note ${Date.now()}`;
      await h.page.getByRole("textbox", { name: "Add a comment" }).fill(note);
      const [saved] = await Promise.all([
        h.page.waitForResponse(
          (r) => r.url().includes(`/incidents/${s.main.id}/comments`) && r.request().method() === "POST",
        ),
        h.page.getByRole("button", { name: /^comment$/i }).click(),
      ]);
      assert.equal(saved.status(), 201, `comment POST: ${saved.status()} ${await saved.text()}`);
      const comments = await api(h.request, "get", `/incidents/${s.main.id}/comments`, { token: h.auth.token });
      const posted = comments.items.find((c) => c.body === note);
      assert.ok(posted, "the comment must be saved");
      assert.equal(posted.author_user_id, s.me.id, "the owner wrote the comment");
      const after = (await chain(h, s.main.id)).lastActivity;
      assert.ok(
        Date.parse(after) > Date.parse(before),
        `last_activity_at must move forward (before ${before}, after ${after})`,
      );
    });

    await h.step("release resumes escalation at the next level", async () => {
      await strip(h).getByRole("button", { name: /^release$/i }).click();
      await strip(h).getByRole("button", { name: /^take$/i }).waitFor({ state: "visible" });
      const resumed = await chain(h, s.main.id);
      assert.equal(resumed.status, "running");
      assert.deepEqual(resumed.levels, [0, 1]);
      assert.equal(await owner(h, s.main.id), null);
      await openIncident(h, s.main.id, s.main.title);
      await h.expectText("Released the incident; escalation resumed.");
      await capture(h, "lifecycle-released");
    });

    await h.step("acknowledging again takes the lock back", async () => {
      await strip(h).getByRole("button", { name: /^acknowledge$/i }).click();
      await strip(h).getByText("You own this").waitFor({ state: "visible" });
      assert.equal((await chain(h, s.main.id)).status, "acked");
      assert.equal(await owner(h, s.main.id), s.me.id);
    });

    await h.step("resolve stops the chain for good", async () => {
      await strip(h).getByRole("button", { name: /^resolve$/i }).click();
      await strip(h).getByText("Resolved").first().waitFor({ state: "visible" });
      const closed = await chain(h, s.main.id);
      assert.equal(closed.status, "cancelled");
      await h.page.waitForTimeout(PAST_A_LEVEL_MS);
      assert.deepEqual((await chain(h, s.main.id)).levels, closed.levels);
      await capture(h, "lifecycle-resolved");
    });

    await h.step("bulk acknowledge and resolve from the incident list", async () => {
      const prefix = qaName("lifecycle-bulk");
      const a = await newIncident("lifecycle-bulk-a");
      const b = await newIncident("lifecycle-bulk-b");
      await h.goto("/dashboard/incidents");
      await h.page.getByRole("textbox", { name: "Search incidents" }).fill(prefix);
      await h.page.locator(`a[href*="id=${a.id}"]:visible`).first().waitFor({ state: "visible" });
      await h.page.locator(`a[href*="id=${b.id}"]:visible`).first().waitFor({ state: "visible" });

      const selectBoth = async () => {
        const all = h.page.getByRole("checkbox", { name: "Select all rows on this page" });
        if (!(await all.isChecked())) await all.check();
      };
      await selectBoth();
      await h.page.getByTestId("incident-actions-trigger").click();
      await h.page.getByTestId("incident-action-acknowledge").click();
      for (const incident of [a, b]) {
        await expectEventually(async () => (await chain(h, incident.id)).status === "acked");
        assert.equal(await owner(h, incident.id), s.me.id);
      }

      await selectBoth();
      await h.page.getByTestId("incident-actions-trigger").click();
      await h.page.getByTestId("incident-action-resolve").click();
      // The menu closes on click; the confirmation modal's button is the only
      // "Mark as resolved" button left on the page.
      await h.page.getByRole("button", { name: /^mark as resolved$/i }).last().click();
      for (const incident of [a, b]) {
        await expectEventually(async () => (await chain(h, incident.id)).status === "cancelled");
      }
      await capture(h, "lifecycle-bulk-resolved");
    });

    await h.step("only the current owner can confirm a takeover", async () => {
      const t = await newIncident("lifecycle-takeover");
      await api(h.request, "post", `/incidents/${t.id}/ack`, { token: h.auth.token, data: { via: "web_ui" } });
      await api(h.request, "post", `/incidents/${t.id}/take`, { token: s.otherToken, data: {} });
      await api(h.request, "post", `/incidents/${t.id}/take`, {
        token: s.otherToken,
        data: { confirm: true },
        expect: 403,
      });
      assert.equal(await owner(h, t.id), s.me.id);
      await api(h.request, "post", `/incidents/${t.id}/take`, {
        token: h.auth.token,
        data: { confirm: true },
      });
      assert.equal(await owner(h, t.id), s.other.id);
      assert.equal((await chain(h, t.id)).status, "acked");
      await openIncident(h, t.id, t.title);
      await strip(h).getByRole("button", { name: /^take over$/i }).waitFor({ state: "visible" });
      await h.expectText("Handed the incident to");
      await capture(h, "lifecycle-takeover");
    });

    if (config.cleanup) {
      await h.step("clean up lifecycle fixtures", async () => {
        for (const id of s.incidents) {
          await api(h.request, "delete", `/incidents/${id}`, { token: h.auth.token });
        }
        if (s.other) {
          await api(h.request, "patch", `/auth/users/${s.other.id}`, {
            token: h.auth.token,
            data: { is_active: false },
          });
        }
      });
    }
  },
};

async function expectEventually(check, { timeoutMs = 10000, intervalMs = 250 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  assert.fail("condition not met in time");
}
