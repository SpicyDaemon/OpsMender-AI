// Feature: authentication. Exercises the real login form, lands on the
// dashboard, and captures the session token/org for optional cleanup later.

import { config } from "../lib/config.mjs";

export default {
  id: "auth",
  title: "Authentication — login",
  async run(h) {
    await h.step("login page renders", async () => {
      await h.goto("/login");
      await h.expectText("Sign in to OpsMender");
    });

    await h.step("sign in with credentials", async () => {
      await h.page.locator("#username").fill(config.username);
      await h.page.locator("#password").fill(config.password);
      await Promise.all([
        h.page.waitForURL("**/dashboard**", { timeout: config.defaultTimeout }),
        h.clickButton(/sign in/i),
      ]);
    });

    await h.step("session established", async () => {
      const token = await h.page.evaluate(() =>
        localStorage.getItem("opsmender_token"),
      );
      const orgId = await h.page.evaluate(() =>
        localStorage.getItem("opsmender_org_id"),
      );
      if (!token) throw new Error("no opsmender_token in localStorage after login");
      h.auth = { token, orgId };
    });
  },
};
