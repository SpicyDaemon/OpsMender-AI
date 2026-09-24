// Browser proof for webhook recovery and cross-service collision handling.
// Guarded by QA_INTAKE_PAGING because its Escalation Chains page the QA user.
// Run it against a disposable instance with local notification sinks.

import assert from "node:assert/strict";
import path from "node:path";
import { config, qaName, qaSlug } from "../lib/config.mjs";
import { Harness } from "../lib/harness.mjs";

async function checked(request, method, path, { auth, data } = {}) {
  const response = await request[method](`${config.baseUrl}${path}`, {
    ...(auth ? { headers: { Authorization: `Bearer ${auth.token}` } } : {}),
    ...(data ? { data } : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok()) {
    throw new Error(`${method.toUpperCase()} ${path}: ${response.status()} ${JSON.stringify(body)}`);
  }
  return body;
}

async function createService(h, label, userId) {
  const team = await checked(h.request, "post", "/teams", {
    auth: h.auth,
    data: { name: qaName(`${label}-team`), slug: qaSlug(`${label}-team`) },
  });
  const chain = await checked(h.request, "post", "/escalation-chains", {
    auth: h.auth,
    data: { name: qaName(`${label}-chain`), team_id: team.id },
  });
  await checked(h.request, "post", `/escalation-chains/${chain.id}/steps`, {
    auth: h.auth,
    data: { step_index: 0, target_type: "user", target_id: userId, timeout_seconds: 300 },
  });
  const service = await checked(h.request, "post", "/services", {
    auth: h.auth,
    data: {
      name: qaName(`${label}-service`),
      slug: qaSlug(`${label}-service`),
      team_id: team.id,
      priority: "P1",
      alert_grouping: "off",
    },
  });
  await checked(h.request, "post", `/services/${service.id}/escalation-chains`, {
    auth: h.auth,
    data: { chain_id: chain.id },
  });
  return service;
}

async function createToken(h, label, provider, service) {
  return checked(h.request, "post", "/ingest-tokens", {
    auth: h.auth,
    data: { name: qaName(label), provider, service_id: service.id },
  });
}

async function webhook(h, token, payload) {
  const response = await h.request.post(`${config.baseUrl}/incidents/ingest`, {
    headers: { "X-OpsMender-Token": token.token },
    data: payload,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok()) throw new Error(`Webhook: ${response.status()} ${JSON.stringify(body)}`);
  return body;
}

async function serviceWebhook(h, service, payload) {
  assert.ok(service.intake_url?.startsWith("/api/v1/intake/"));
  const response = await h.request.post(`${config.baseUrl}${service.intake_url}`, {
    data: payload,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok()) throw new Error(`Service intake: ${response.status()} ${JSON.stringify(body)}`);
  return body;
}

function sns(alarmName, state, messageId) {
  return {
    Type: "Notification",
    MessageId: messageId,
    Message: JSON.stringify({
      AlarmName: alarmName,
      AWSAccountId: "000000000000",
      Region: "us-east-1",
      NewStateValue: state,
      NewStateReason: "Synthetic threshold",
    }),
  };
}

async function capture(h, label) {
  await h.page.screenshot({
    path: path.join(config.reportDir, "screenshots", `${config.runId}-${label}.png`),
    fullPage: true,
  });
}

export default {
  id: "intake_correctness",
  title: "Intake — recovery and collision",
  async run(h) {
    if (!config.intakePaging) {
      await h.step("intake recovery and collision checks", async () => {
        throw Harness.skip("QA_INTAKE_PAGING not enabled");
      });
      return;
    }
    const state = {};
    await h.step("prepare isolated service and webhook fixtures", async () => {
      const me = await checked(h.request, "get", "/auth/me", { auth: h.auth });
      state.owner = await createService(h, "intake-owner", me.id);
      state.loser = await createService(h, "intake-receiver", me.id);
      state.ownerToken = await createToken(h, "intake-provider-owner", "generic", state.owner);
      state.loserToken = await createToken(h, "intake-provider-receiver", "generic", state.loser);
    });

    await h.step("SNS alarm and duplicate open one incident", async () => {
      state.alarm = qaName("cpu-alarm");
      state.first = await serviceWebhook(h, state.owner, sns(state.alarm, "ALARM", "delivery-1"));
      const duplicate = await serviceWebhook(h, state.owner, sns(state.alarm, "ALARM", "delivery-2"));
      assert.equal(state.first.dedup_action, "created");
      assert.equal(duplicate.incident_id, state.first.incident_id);
      const incident = await checked(h.request, "get", `/incidents/${state.first.incident_id}`, { auth: h.auth });
      assert.equal(incident.status, "open");
      assert.equal(incident.external_id, `000000000000:us-east-1:${state.alarm}`);
      await h.goto(`/dashboard/incidents/detail?id=${state.first.incident_id}`);
      await h.expectText(state.alarm);
      await capture(h, "first-alarm");
    });

    await h.step("acknowledge the first alarm in the browser", async () => {
      await h.page.getByRole("button", { name: /^acknowledge$/i }).first().click();
      await h.page.getByText("You own this").first().waitFor({ state: "visible" });
      const incident = await checked(h.request, "get", `/incidents/${state.first.incident_id}`, { auth: h.auth });
      assert.ok(incident.acknowledged_at);
    });

    await h.step("SNS recovery resolves and re-fire opens a new incident", async () => {
      const clear = await serviceWebhook(h, state.owner, sns(state.alarm, "OK", "delivery-3"));
      state.refire = await serviceWebhook(h, state.owner, sns(state.alarm, "ALARM", "delivery-4"));
      assert.equal(clear.incident_id, state.first.incident_id);
      assert.equal(clear.dedup_action, "updated");
      assert.equal(state.refire.dedup_action, "created");
      assert.notEqual(state.refire.incident_id, state.first.incident_id);
      const oldIncident = await checked(h.request, "get", `/incidents/${state.first.incident_id}`, { auth: h.auth });
      const newIncident = await checked(h.request, "get", `/incidents/${state.refire.incident_id}`, { auth: h.auth });
      assert.equal(oldIncident.status, "resolved");
      assert.equal(newIncident.status, "open");
      await h.goto(`/dashboard/incidents/detail?id=${state.refire.incident_id}`);
      await h.expectText(state.alarm);
      await capture(h, "refired-alarm");
    });

    await h.step("incident list filters show resolved and open alarms", async () => {
      await h.goto("/dashboard/incidents");
      await h.page.getByRole("textbox", { name: "Search incidents" }).fill(state.alarm);
      await h.page.getByRole("button", { name: "All statuses" }).click();
      await h.page.getByRole("checkbox", { name: "Resolved" }).check();
      await h.page.locator(`a[href*="id=${state.refire.incident_id}"]:visible`).first().waitFor({ state: "hidden" });
      await h.page.locator(`a[href*="id=${state.first.incident_id}"]:visible`).first().waitFor({ state: "visible" });
      await capture(h, "resolved-filter");
      await h.page.getByRole("checkbox", { name: "Resolved" }).uncheck();
      await h.page.getByRole("checkbox", { name: "Open" }).check();
      await h.page.locator(`a[href*="id=${state.first.incident_id}"]:visible`).first().waitFor({ state: "hidden" });
      await h.page.locator(`a[href*="id=${state.refire.incident_id}"]:visible`).first().waitFor({ state: "visible" });
      await capture(h, "open-filter");
    });

    await h.step("cross-service alert reaches the receiver Inbox once", async () => {
      const payload = { title: qaName("shared-alarm"), id: qaName("shared-id"), severity: "high" };
      state.owned = await webhook(h, state.ownerToken, payload);
      const before = await checked(h.request, "get", "/notifications", { auth: h.auth });
      const folded = await webhook(h, state.loserToken, payload);
      const replay = await webhook(h, state.loserToken, payload);
      assert.equal(folded.incident_id, state.owned.incident_id);
      assert.equal(replay.incident_id, state.owned.incident_id);
      assert.equal(folded.dedup_action, "skipped");
      const after = await checked(h.request, "get", "/notifications", { auth: h.auth });
      const notices = after.items.filter((item) => item.event_type === "incident.collision" && item.incident_id === state.owned.incident_id);
      assert.equal(notices.length, 1);
      assert.equal(notices[0].link, `/dashboard/incidents/detail?id=${state.owned.incident_id}`);
      assert.equal(after.total, before.total + 1);
      await h.goto("/dashboard/incidents");
      await h.page.getByRole("button", { name: /inbox/i }).first().click();
      await h.expectText("Alert assigned to another service");
      await capture(h, "receiver-inbox");
      await h.page.getByRole("button", { name: /Alert assigned to another service/i }).first().click();
      await h.page.waitForURL(`**/dashboard/incidents/detail?id=${state.owned.incident_id}`);
      await h.expectText(payload.title);
      await capture(h, "owned-incident-from-inbox");
    });

    await h.step("simultaneous collision requests keep one durable notice", async () => {
      const payload = { title: qaName("raced-alarm"), id: qaName("raced-id"), severity: "high" };
      const owner = await webhook(h, state.ownerToken, payload);
      const [one, two] = await Promise.all([
        webhook(h, state.loserToken, payload),
        webhook(h, state.loserToken, payload),
      ]);
      assert.equal(one.incident_id, owner.incident_id);
      assert.equal(two.incident_id, owner.incident_id);
      const inbox = await checked(h.request, "get", "/notifications", { auth: h.auth });
      assert.equal(inbox.items.filter((item) => item.event_type === "incident.collision" && item.incident_id === owner.incident_id).length, 1);
    });

    // The run-prefix sweep can't find the CloudWatch-titled incidents or the
    // ingest tokens, so remove them here. Tokens with deliveries can't be
    // deleted, only revoked; a revoked token no longer accepts alerts.
    if (config.cleanup) {
      await h.step("clean up intake fixtures", async () => {
        for (const id of [state.first?.incident_id, state.refire?.incident_id]) {
          if (id) await checked(h.request, "delete", `/incidents/${id}`, { auth: h.auth });
        }
        const serviceIds = [state.owner?.id, state.loser?.id].filter(Boolean);
        const tokens = await checked(h.request, "get", "/ingest-tokens", { auth: h.auth });
        const mine = tokens.items.filter(
          (token) =>
            token.is_active &&
            (token.id === state.ownerToken?.id ||
              token.id === state.loserToken?.id ||
              serviceIds.some((id) => token.name === `service:${id}`)),
        );
        for (const token of mine) {
          await checked(h.request, "post", `/ingest-tokens/${token.id}/revoke`, { auth: h.auth });
        }
        // Two provider tokens plus the owner's intake-URL token.
        assert.ok(mine.length >= 3);
        const after = await checked(h.request, "get", "/ingest-tokens", { auth: h.auth });
        const ids = new Set(mine.map((token) => token.id));
        assert.equal(after.items.filter((token) => ids.has(token.id) && token.is_active).length, 0);
      });
    }
  },
};
