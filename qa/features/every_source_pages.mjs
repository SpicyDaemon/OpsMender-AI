// Browser proof for "every incident source pages": a new service starts at P1
// and says which priorities page, and a Maintenance Window recurrence rule that
// can't be read is refused. Saves nothing and pages nobody.

import assert from "node:assert/strict";

export default {
  id: "every_source_pages",
  title: "Paging — priorities and recurring windows",
  async run(h) {
    await h.step("a new service starts at P1 and explains which priorities page", async () => {
      await h.goto("/dashboard/paging/services");
      const newBtn = h.page.getByRole("button", { name: /new service/i }).first();
      await newBtn.waitFor({ state: "visible" });
      await newBtn.click();
      // The modal has no dialog role; its Priority select is the one listing
      // "P0 Critical".
      const priority = h.page
        .locator("select")
        .filter({ has: h.page.locator("option", { hasText: "P0 Critical" }) })
        .first();
      await priority.waitFor({ state: "visible" });
      assert.equal(await priority.inputValue(), "P1");
      await h.page.getByText(/P2 and P3 don.t page/).waitFor({ state: "visible" });
      await h.page.getByRole("button", { name: /^close /i }).first().click();
    });

    await h.step("a recurrence rule that can't be read is refused", async () => {
      await h.goto("/dashboard/reliability");
      await h.page.getByRole("button", { name: /new maintenance window/i }).first().click();
      const page = h.page;
      await page.locator("#mw-name").waitFor({ state: "visible" });
      await page.locator("#mw-name").fill("QA recurrence check");
      await page.locator("#mw-start").fill("2030-01-06T02:00");
      await page.locator("#mw-end").fill("2030-01-06T04:00");
      await page.locator("#mw-rrule").fill("FREQ=SOMETIMES");
      const [saved] = await Promise.all([
        h.page.waitForResponse(
          (r) => r.url().includes("/maintenance-windows") && r.request().method() === "POST",
        ),
        page.getByRole("button", { name: /^schedule$/i }).click(),
      ]);
      assert.equal(saved.status(), 422, `expected 422, got ${saved.status()}`);
      await page.getByText(/recurrence rule is not valid/i).waitFor({ state: "visible" });
      await page.getByRole("button", { name: /^cancel$/i }).click();
    });
  },
};
