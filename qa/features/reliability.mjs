// Feature: reliability. Creates an HTTP SLA target and confirms it appears.

import { qaName } from "../lib/config.mjs";

export default {
  id: "reliability",
  title: "Reliability — SLA targets",
  async run(h) {
    const name = qaName("sla");
    h.state.slaName = name;

    await h.step("reliability page loads", async () => {
      await h.goto("/dashboard/reliability");
      await h.expectText(/reliability|target|uptime/i);
    });

    await h.step("create SLA target", async () => {
      const newBtn = h.page.getByRole("button", { name: /new target/i });
      if (!(await newBtn.count())) {
        throw new Error("New Target button not found (need admin role?)");
      }
      await newBtn.first().click();
      await h.page.locator("#target-name").fill(name);
      // Default kind is HTTP; fill its URL.
      const url = h.page.locator("#http-url");
      if (await url.count()) await url.fill("https://example.com/health");
      const status = h.page.locator("#http-status");
      if (await status.count()) await status.fill("200");
      await h.clickButton(/create target/i);
      await h.expectText(name);
    });
  },
};
