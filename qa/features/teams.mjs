// Feature: teams. Creates a QA team and adds the current user so a roster can
// later draw eligible on-call members from it.

import { config, qaName, qaSlug } from "../lib/config.mjs";

export default {
  id: "teams",
  title: "Paging — teams",
  async run(h) {
    const name = qaName("team");
    const slug = qaSlug("team");
    h.state.teamName = name;

    await h.step("teams page loads", async () => {
      await h.goto("/dashboard/paging/teams");
      await h.expectText(/teams/i);
    });

    await h.step("create team", async () => {
      const newBtn = h.page.getByRole("button", { name: /new team/i });
      if (!(await newBtn.count())) {
        throw new Error("New team button not found (need admin role?)");
      }
      await newBtn.first().click();
      await h.fill(name, { label: "Name" });
      await h.fill(slug, { placeholder: "payments-team" });

      // Add the logged-in user to the team so it has an eligible on-call
      // member. Best-effort — the roster feature needs at least one member.
      await h.checkMultiOption("Team members", config.username).catch(() => {});

      await h.clickButton(/^create$/i);
      await h.expectText(/team created/i);
      h.state.teamCreated = true;
    });
  },
};
