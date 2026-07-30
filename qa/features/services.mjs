// Feature: services. Creates a QA service owned by the QA team.

import { Harness } from "../lib/harness.mjs";
import { qaName, qaSlug } from "../lib/config.mjs";

export default {
  id: "services",
  title: "Paging — services",
  async run(h) {
    const name = qaName("svc");
    const slug = qaSlug("svc");
    h.state.serviceName = name;

    await h.step("services page loads", async () => {
      await h.goto("/dashboard/paging/services");
      await h.expectText(/services/i);
    });

    await h.step("create service", async () => {
      const newBtn = h.page.getByRole("button", { name: /new service/i });
      const appeared = await newBtn
        .first()
        .waitFor({ state: "visible", timeout: 20000 })
        .then(() => true)
        .catch(() => false);
      if (!appeared) {
        throw new Error("New service button not found (need admin role?)");
      }
      if (await newBtn.first().isDisabled()) {
        throw Harness.skip("New service disabled — no team available");
      }
      await newBtn.first().click();

      // Owning team — prefer the QA team, else leave the default selection.
      if (h.state.teamName) {
        await h
          .select({ label: h.state.teamName }, { label: "Owning team" })
          .catch(() => {});
      }

      await h.fill(name, { label: "Name" });
      await h.fill(slug, { placeholder: "payments-api" });
      await h.clickButton(/^create$/i);
      await h.expectText(/service created/i);
      h.state.serviceCreated = true;
    });
  },
};
