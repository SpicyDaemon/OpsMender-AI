// Feature: logout. Signs out via the sidebar control (or the top-bar user
// menu as a fallback) and verifies the session is torn down.

import { config } from "../lib/config.mjs";

export default {
  id: "logout",
  title: "Authentication — logout",
  async run(h) {
    await h.step("sign out", async () => {
      await h.goto("/dashboard");
      // Preferred: the sidebar sign-out icon button (title="Sign out").
      let signOut = h.page.locator('button[title="Sign out"]').first();
      if (!(await signOut.count())) {
        // Fallback: open the top-bar user menu (the user chip), then the item.
        const chip = h.page
          .getByRole("button", { name: /account menu/i })
          .first();
        await chip.waitFor({ state: "visible", timeout: config.defaultTimeout });
        await chip.click();
        signOut = h.page.getByRole("button", { name: /sign out/i }).first();
        await signOut.waitFor({ state: "visible", timeout: config.defaultTimeout });
      }
      await Promise.all([
        h.page
          .waitForURL("**/login**", { timeout: config.defaultTimeout })
          .catch(() => {}),
        signOut.click(),
      ]);
    });

    await h.step("session cleared", async () => {
      await h.page.waitForTimeout(500);
      const token = await h.page.evaluate(() => localStorage.getItem("opsmender_token"));
      if (token) throw new Error("opsmender_token still present after logout");
      if (!/\/login/.test(h.page.url())) {
        // Not fatal if still settling, but flag it.
        throw new Error(`expected /login after logout, got ${h.page.url()}`);
      }
    });
  },
};
