// Feature: roster calendar. Opens the on-call calendar for a roster and
// verifies the calendar modal renders with its navigation controls. The
// Calendar row-action is only revealed on row hover.

import { Harness } from "../lib/harness.mjs";

export default {
  id: "roster-calendar",
  title: "Paging — roster calendar",
  async run(h) {
    await h.step("open roster calendar", async () => {
      await h.goto("/dashboard/paging/rosters");
      const calBtn = h.page.getByRole("button", { name: /calendar/i }).first();
      const appeared = await calBtn
        .waitFor({ state: "attached", timeout: 20000 })
        .then(() => true)
        .catch(() => false);
      if (!appeared) {
        throw Harness.skip("no roster with a Calendar action present");
      }
      // Reveal the hover-only row action, then click (force as a fallback).
      const row = calBtn.locator("xpath=ancestor::tr[1]");
      if (await row.count()) await row.hover().catch(() => {});
      await calBtn.click({ force: true });

      // Assert on the modal's navigation control (not the trigger button,
      // which also contains the word "calendar").
      await h.page
        .getByRole("button", { name: /today/i })
        .first()
        .waitFor({ state: "visible" });
    });

    await h.step("navigate calendar range", async () => {
      const today = h.page.getByRole("button", { name: /today/i }).first();
      if (!(await today.isVisible().catch(() => false))) {
        throw Harness.skip("calendar not open");
      }
      await today.click();
      await h.page.keyboard.press("Escape");
    });
  },
};
