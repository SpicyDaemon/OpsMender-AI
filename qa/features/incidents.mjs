// Feature: incidents. Creates a (synthetic) incident, opens its detail page,
// and walks the acknowledge → resolve lifecycle.

import { Harness } from "../lib/harness.mjs";
import { config, qaName } from "../lib/config.mjs";

export default {
  id: "incidents",
  title: "Incidents — create & lifecycle",
  async run(h) {
    const title = qaName("incident");

    await h.step("incidents page loads", async () => {
      await h.goto("/dashboard/incidents");
      await h.expectText(/incident/i);
    });

    await h.step("create incident", async () => {
      if (config.fireTestIncident) {
        const fireBtn = h.page.getByRole("button", { name: /fire test incident/i }).first();
        if (!(await fireBtn.count())) throw new Error("Fire Test Incident button not found");
        await fireBtn.click();
        // Optional service selector inside the modal.
        const svc = h.page.locator("#test-service");
        if ((await svc.count()) && h.state.serviceName) {
          await svc.selectOption({ label: h.state.serviceName }).catch(() => {});
        }
        // Confirm button shares the "Fire Test Incident" label.
        await h.page
          .getByRole("button", { name: /fire test incident/i })
          .last()
          .click();
        await h.expectText(/incident|created|fired/i);
      } else {
        await h.page.getByRole("button", { name: /new incident/i }).first().click();
        const svc = h.page.locator("#ci-service");
        if (await svc.count()) {
          if (h.state.serviceName) {
            await svc.selectOption({ label: h.state.serviceName }).catch(async () => {
              await svc.selectOption({ index: 1 }).catch(() => {});
            });
          } else {
            await svc.selectOption({ index: 1 }).catch(() => {});
          }
        }
        await h.page.locator("#ci-title").fill(title);
        await h.page.locator("#ci-desc").fill("Automated QA walkthrough incident.");
        await h.clickButton(/^create$/i);
        await h.expectText(/incident created/i);
      }
    });

    await h.step("open incident detail", async () => {
      await h.goto("/dashboard/incidents");
      const link = h.page.locator('a[href*="/dashboard/incidents/detail?id="]').first();
      if (!(await link.count())) throw Harness.skip("no incident rows to open");
      const href = await link.getAttribute("href");
      await h.goto(href);
      await h.expectText(/incident|severity|status/i);
    });

    await h.step("acknowledge incident", async () => {
      const ack = h.page.getByRole("button", { name: /^acknowledge$/i }).first();
      if (!(await ack.count())) throw Harness.skip("no Acknowledge action (already acked?)");
      await ack.click();
      await h.page.waitForTimeout(800);
    });

    await h.step("resolve incident", async () => {
      const resolve = h.page.getByRole("button", { name: /^resolve$/i }).first();
      if (!(await resolve.count())) throw Harness.skip("no Resolve action available");
      await resolve.click();
      // Some flows pop a confirm dialog.
      const confirm = h.page.getByRole("button", { name: /^(resolve|confirm)$/i }).last();
      if (await confirm.count()) await confirm.click().catch(() => {});
      await h.page.waitForTimeout(800);
    });
  },
};
