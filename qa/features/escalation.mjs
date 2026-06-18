// Feature: escalation chains (a.k.a. escalation policies). Creates a chain
// scoped to the QA team.

import { qaName } from "../lib/config.mjs";

export default {
  id: "escalation",
  title: "Paging — escalation policies",
  async run(h) {
    const name = qaName("chain");
    h.state.chainName = name;

    await h.step("escalation chains page loads", async () => {
      await h.goto("/dashboard/paging/escalation-chains");
      await h.expectText(/escalation|chain/i);
    });

    await h.step("create escalation chain", async () => {
      const newBtn = h.page.getByRole("button", { name: /new chain/i });
      if (!(await newBtn.count())) {
        throw new Error("New chain button not found (need admin role?)");
      }
      await newBtn.first().click();

      const teamSpec = h.state.teamName
        ? { label: h.state.teamName }
        : { index: 0 };
      await h.select(teamSpec, { label: "Team" }).catch(() => {});

      await h.fill(name, { placeholder: "Primary on-call escalation" });
      await h.clickButton(/^create$/i);
      await h.expectText(/chain created/i);
    });
  },
};
