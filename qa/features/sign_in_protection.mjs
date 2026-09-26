// Browser proof for sign-in protection: after repeated failed sign-ins for one
// account, the login page refuses even the right password and says how long to
// wait. Uses a throwaway operator (deactivated at the end); pages nobody.

import assert from "node:assert/strict";
import crypto from "node:crypto";
import { config, qaSlug } from "../lib/config.mjs";

async function api(h, method, route, data) {
  const response = await h.request[method](`${config.baseUrl}${route}`, {
    headers: { Authorization: `Bearer ${h.auth.token}` },
    ...(data ? { data } : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok()) {
    throw new Error(`${method.toUpperCase()} ${route}: ${response.status()} ${JSON.stringify(body)}`);
  }
  return body;
}

export default {
  id: "sign_in_protection",
  title: "Auth — sign-in protection",
  async run(h) {
    const s = {};

    await h.step("repeated failures lock that account on this address", async () => {
      s.username = qaSlug("lockout");
      s.password = `Qa-${crypto.randomBytes(12).toString("hex")}`;
      s.user = await api(h, "post", "/auth/users", {
        username: s.username,
        email: `${s.username}@example.com`,
        role: "operator",
        password: s.password,
        require_password_change: false,
      });
      // A fresh browser context: the harness's own session stays signed in.
      const context = await h.browser.newContext({ baseURL: config.baseUrl });
      try {
        const page = await context.newPage();
        await page.goto("/login");
        const submit = async (password) => {
          // The field is an email input, so sign in by email.
          await page.locator("#username").fill(`${s.username}@example.com`);
          await page.locator("#password").fill(password);
          const [response] = await Promise.all([
            page.waitForResponse((r) => r.url().endsWith("/auth/login")),
            page.getByRole("button", { name: /^sign in$/i }).click(),
          ]);
          return response.status();
        };
        const limit = Number(process.env.OPSMENDER_SIGNIN_MAX_FAILURES || 5);
        for (let i = 0; i < limit; i += 1) {
          assert.equal(await submit("definitely-wrong"), 401);
        }
        assert.equal(await submit(s.password), 429, "the right password is refused while locked");
        await page.getByText(/Too many failed attempts\. Try again in \d+ minutes?\./).waitFor({
          state: "visible",
        });
      } finally {
        await context.close();
      }
    });

    await h.step("deactivate the throwaway user", async () => {
      if (s.user) await api(h, "patch", `/auth/users/${s.user.id}`, { is_active: false });
    });
  },
};
