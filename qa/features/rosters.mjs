// Feature: rosters (on-call rotation). Creates a weekly roster on the QA team.
// The rotation-members multiselect requires an eligible Admin/Operator member
// on the team — added during the teams feature.

import { config, qaName } from "../lib/config.mjs";

function today() {
  return new Date().toISOString().slice(0, 10); // yyyy-mm-dd
}

export default {
  id: "rosters",
  title: "Paging — rosters (on-call)",
  async run(h) {
    const name = qaName("roster");
    h.state.rosterName = name;

    await h.step("rosters page loads", async () => {
      await h.goto("/dashboard/paging/rosters");
      await h.expectText(/roster/i);
    });

    await h.step("create roster", async () => {
      const newBtn = h.page.getByRole("button", { name: /new roster/i });
      if (!(await newBtn.count())) {
        throw new Error("New roster button not found (need admin role?)");
      }
      await newBtn.first().click();

      // Team (drives the eligible-members list).
      const teamSpec = h.state.teamName
        ? { label: h.state.teamName }
        : { index: 0 };
      await h.select(teamSpec, { label: "Team" }).catch(() => {});

      await h.fill(name, { placeholder: "Primary on-call" });
      // Coverage + schedule fields (placeholders from the create modal).
      await h.fill("UTC", { placeholder: "America/Chicago" }).catch(() => {});
      await h.fill("08:00", { placeholder: "08:00" }).catch(() => {});
      await h.fill("18:00", { placeholder: "18:00" }).catch(() => {});
      const dateInput = h.page.locator('input[type="date"]').first();
      if (await dateInput.count()) await dateInput.fill(today());

      // Rotation members — tick the current user. The multiselect's aria-label
      // may be "Rotation members" or similar; try a couple of variants.
      const picked =
        (await h.checkMultiOption("Rotation members", config.username).catch(() => false)) ||
        (await h.checkMultiOption("Rotation members (ordered)", config.username).catch(() => false));
      if (!picked) {
        // Fall back to the first checkbox in any multiselect on the form.
        await h.page
          .locator('[aria-label*="member" i] input[type="checkbox"]')
          .first()
          .check()
          .catch(() => {});
      }

      await h.clickButton(/^create$/i);
      await h.expectText(/roster created/i);
      h.state.rosterCreated = true;
    });
  },
};
