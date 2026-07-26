// Feature: skills. Covers the v1.1 source-neutral Skill Studio authoring surface.

import { Harness } from "../lib/harness.mjs";

export default {
  id: "skills",
  title: "AI — skills",
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
  },
};
