// Browser proof for "who's on call": the Services table's "On call now"
// matches the Roster API at the current time, and the real routing form still
// saves under the preference validation (and the API rejects a bad zone). Creates no incidents and pages nobody.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { config, qaName, qaSlug } from "../lib/config.mjs";

async function api(request, method, route, { token, data } = {}) {
  const response = await request[method](`${config.baseUrl}${route}`, {
    ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    ...(data ? { data } : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok()) {
    throw new Error(`${method.toUpperCase()} ${route}: ${response.status()} ${JSON.stringify(body)}`);
  }
  return body;
}

function yesterdayUtc() {
  return new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
}

export default {
  id: "on_call_consistency",
  title: "Paging — who's on call",
  async run(h) {
    const s = {};

    await h.step("prepare a round-the-clock Roster on a service's chain", async () => {
      const token = h.auth.token;
      s.me = await api(h.request, "get", "/auth/me", { token });
      s.otherName = qaSlug("oncall-peer");
      s.other = await api(h.request, "post", "/auth/users", {
        token,
        data: {
          username: s.otherName,
          email: `${s.otherName}@example.com`,
          role: "operator",
          password: `Qa-${crypto.randomBytes(12).toString("hex")}`,
          require_password_change: false,
        },
      });
      const team = await api(h.request, "post", "/teams", {
        token,
        data: { name: qaName("oncall-team"), slug: qaSlug("oncall-team") },
      });
      for (const user of [s.me, s.other]) {
        await api(h.request, "post", `/teams/${team.id}/members`, { token, data: { user_id: user.id } });
      }
      s.roster = await api(h.request, "post", "/rosters", {
        token,
        data: {
          team_id: team.id,
          name: qaName("oncall-roster"),
          pattern: "daily",
          pattern_length: 1,
          anchor_date: yesterdayUtc(),
          time_zone: "UTC",
          coverage_start_time: "00:00",
          coverage_end_time: "00:00",
        },
      });
      for (const [index, user] of [s.me, s.other].entries()) {
        await api(h.request, "post", `/rosters/${s.roster.id}/members`, {
          token,
          data: { user_id: user.id, position_index: index },
        });
      }
      const chain = await api(h.request, "post", "/escalation-chains", {
        token,
        data: { name: qaName("oncall-chain"), team_id: team.id },
      });
      await api(h.request, "post", `/escalation-chains/${chain.id}/steps`, {
        token,
        data: { step_index: 0, target_type: "roster", target_id: s.roster.id, timeout_seconds: 300 },
      });
      s.serviceName = qaName("oncall-service");
      const service = await api(h.request, "post", "/services", {
        token,
        data: { name: s.serviceName, slug: qaSlug("oncall-service"), team_id: team.id, priority: "P1" },
      });
      await api(h.request, "post", `/services/${service.id}/escalation-chains`, {
        token,
        data: { chain_id: chain.id },
      });
    });

    await h.step("Services 'On call now' matches the Roster API at the current time", async () => {
      const now = await api(h.request, "get", `/rosters/${s.roster.id}/on-call`, { token: h.auth.token });
      assert.ok(now.user_id, "a round-the-clock Roster always has someone on call");
      const expected = now.user_id === s.me.id ? s.me.username : s.otherName;
      await h.goto("/dashboard/paging/services");
      await h.page.getByRole("textbox", { name: "Search services" }).fill(s.serviceName);
      const row = h.page.getByRole("row", { name: new RegExp(s.serviceName) }).first();
      await row.waitFor({ state: "visible" });
      await row.getByText(expected, { exact: true }).waitFor({ state: "visible" });
    });

    await h.step("routing form saves quiet hours; the API rejects a bad time zone", async () => {
      const token = h.auth.token;
      s.prefsBefore = await api(h.request, "get", "/users/me/notification-preferences", { token });
      await h.goto("/dashboard/paging/notifications");
      const enable = h.page.getByRole("checkbox", { name: /enable quiet hours/i });
      await enable.waitFor({ state: "visible" });
      if (!(await enable.isChecked())) await enable.check();
      // The time zone is a dropdown, so the form can only send valid zones.
      const zone = h.page
        .locator("select")
        .filter({ has: h.page.locator('option[value="America/Chicago"]') })
        .first();
      await zone.selectOption("America/Chicago");
      const [saved] = await Promise.all([
        h.page.waitForResponse(
          (r) => r.url().includes("/users/me/notification-preferences") && r.request().method() === "PUT",
        ),
        h.page.getByRole("button", { name: /^save routing$/i }).click(),
      ]);
      assert.equal(saved.status(), 200, `the form's own payload: ${saved.status()} ${await saved.text()}`);
      const stored = await api(h.request, "get", "/users/me/notification-preferences", { token });
      assert.equal(stored.quiet_hours?.time_zone, "America/Chicago");

      const bad = await h.request.put(`${config.baseUrl}/users/me/notification-preferences`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { quiet_hours: { ...stored.quiet_hours, time_zone: "Mars/Olympus" } },
      });
      assert.equal(bad.status(), 422, "an invalid time zone is rejected");
      const after = await api(h.request, "get", "/users/me/notification-preferences", { token });
      assert.equal(after.quiet_hours?.time_zone, "America/Chicago", "a rejected save changes nothing");
    });

    await h.step("restore the QA user's preferences", async () => {
      if (!s.prefsBefore) return;
      await api(h.request, "put", "/users/me/notification-preferences", {
        token: h.auth.token,
        data: {
          channels: s.prefsBefore.channels ?? {},
          routing: s.prefsBefore.routing ?? {},
          quiet_hours: s.prefsBefore.quiet_hours ?? null,
        },
      });
      if (config.cleanup && s.other) {
        await api(h.request, "patch", `/auth/users/${s.other.id}`, {
          token: h.auth.token,
          data: { is_active: false },
        });
      }
    });
  },
};
