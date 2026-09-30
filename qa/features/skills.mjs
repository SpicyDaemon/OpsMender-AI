// Feature: skills. Covers the v1.1 source-neutral Skill Studio authoring surface.

import { Harness } from "../lib/harness.mjs";
import { config, qaName } from "../lib/config.mjs";

const blockedPolicy = `---
version: "1"
environment: qa
default_tier: T2
operations:
  - tool: inspect_service
    classification: safe
    tiers:
      T0: { enabled: false, mode: blocked }
      T1: { enabled: true, mode: approval }
      T2: { enabled: false, mode: blocked }
---

# QA policy
`;
const widenedPolicy = blockedPolicy.replace(
  "T0: { enabled: false, mode: blocked }",
  "T0: { enabled: true, mode: autonomous }",
);

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

export default {
  id: "skills",
  title: "AI: skills",
  async run(h) {
    await h.step("skills page loads", async () => {
      await h.goto("/dashboard/skills");
      await h.expectText(/MCP Skill Studio/i);
    });

    await h.step("starter templates and backend validation are exposed", async () => {
      const newSkill = h.page.getByRole("button", { name: /new skill/i }).first();
      if (!(await newSkill.count())) {
        throw Harness.skip("no New skill control (role-gated)");
      }
      await newSkill.click();
      await h.expectText(/Starter template/i);
      await h.expectText(/Content is validated before saving/i);
      const assignments = h.page.locator("#skill-mcp");
      if (!(await assignments.locator('optgroup[label="Integration connectors"]').count())) {
        throw new Error("integration connector assignments are missing");
      }
      await h.page.keyboard.press("Escape");
    });

    await h.step("generator accepts MCP or integration tool sources", async () => {
      await h.page
        .getByRole("button", { name: /generate from tools/i })
        .first()
        .click();
      await h.expectText(/Generate skill from tool source/i);
      const source = h.page.locator("#gen-mcp");
      for (const label of ["MCP servers", "Integration connectors"]) {
        if (!(await source.locator(`optgroup[label="${label}"]`).count())) {
          throw new Error(`${label} source group is missing`);
        }
      }
      await h.page.keyboard.press("Escape");
    });

    await h.step("operation policy diff flags widening before save", async () => {
      const name = qaName("policy-diff");
      const created = await requestJson(h, "POST", "/skills", {
        name,
        description: "QA permission-change review",
        content_md: blockedPolicy,
        assignment: "unassigned",
      });
      await h.goto("/dashboard/skills");
      const row = h.page.locator("tr").filter({ hasText: name }).first();
      await row.waitFor({ state: "visible" });
      await row.getByRole("button", { name: /^edit$/i }).click();
      const dialog = h.page.getByRole("dialog", { name: "Edit skill" });
      await dialog.locator("#skill-name").waitFor({ state: "visible" });
      await h.page.waitForFunction(
        (expected) => document.querySelector("#skill-name")?.value === expected,
        name,
        { timeout: 5000 },
      );
      await dialog.locator("#skill-content").fill(widenedPolicy);
      await dialog.getByText("Permission changes before save").waitFor();
      await dialog.getByText("escalation", { exact: true }).waitFor();
      const diffRow = dialog.locator("tr").filter({ hasText: "inspect_service" });
      await diffRow.getByText(/T0 blocked/).waitFor();
      await diffRow.getByText(/T0 autonomous/).waitFor();
      const beforeSave = await requestJson(h, "GET", `/skills/${created.id}`);
      if (beforeSave.content_md !== blockedPolicy) {
        throw new Error("policy changed before the operator saved it");
      }
      const validation = await requestJson(h, "POST", "/skills/validate", {
        content_md: widenedPolicy,
      });
      if (!validation.valid) {
        throw new Error(`widened policy is invalid: ${validation.issues.map((issue) => issue.message).join("; ")}`);
      }
      const updatedResponse = h.page.waitForResponse(
        (response) =>
          response.url().endsWith(`/skills/${created.id}`) &&
          response.request().method() === "PUT",
      );
      await dialog.getByRole("button", { name: /^save changes$/i }).click();
      const savedResponse = await updatedResponse;
      if (!savedResponse.ok()) {
        throw new Error(`skill save failed with ${savedResponse.status()}`);
      }
      await dialog.waitFor({ state: "hidden" });
      const afterSave = await requestJson(h, "GET", `/skills/${created.id}`);
      if (afterSave.content_md !== widenedPolicy) {
        throw new Error("reviewed policy change was not saved");
      }
    });
  },
};
