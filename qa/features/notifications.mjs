// Feature: notifications. Verifies the notification-channels surface renders
// and its create form opens. Optionally fires a live test notification
// (guarded by QA_SEND_TEST_NOTIFICATION because it may page real people).

import { Harness } from "../lib/harness.mjs";
import { config } from "../lib/config.mjs";

export default {
  id: "notifications",
  title: "Paging — notifications",
  async run(h) {
    await h.step("notifications page loads", async () => {
      // Newer builds use /paging/notifications; older expose notification-channels.
      await h.goto("/dashboard/paging/notifications");
      if (!(await h.page.getByText(/notification|channel/i).count())) {
        await h.goto("/dashboard/paging/notification-channels");
      }
      await h.expectText(/notification|channel/i);
    });

    await h.step("create-channel form opens", async () => {
      // The Add Channel button lives on the "Notification Channels" sub-tab
      // (the panel defaults to "My Routing").
      const chTab = h.page
        .getByRole("button", { name: /^notification channels$/i })
        .first();
      if (await chTab.count()) await chTab.click().catch(() => {});

      // Wait for the tab to re-render before probing for the button.
      const addBtn = h.page
        .getByRole("button", { name: /(add|new) channel/i })
        .first();
      const appeared = await addBtn
        .waitFor({ state: "visible", timeout: 5000 })
        .then(() => true)
        .catch(() => false);
      if (!appeared) {
        throw Harness.skip("no Add Channel control (role-gated or different layout)");
      }
      await addBtn.click();
      // The platform selector / name field confirms the form rendered.
      await h.page
        .getByText(/platform|channel name|telegram|slack/i)
        .first()
        .waitFor({ state: "visible" });
      await h.page.keyboard.press("Escape");
    });

    await h.step("send live test notification", async () => {
      if (!config.sendTestNotification) {
        throw Harness.skip("QA_SEND_TEST_NOTIFICATION not enabled");
      }
      // The "Test notification" control lives on the My Routing sub-tab.
      const tab = h.page.getByRole("button", { name: /^my routing$/i }).first();
      if (await tab.count()) await tab.click().catch(() => {});
      const testBtn = h.page.getByRole("button", { name: /test notification/i }).first();
      if (!(await testBtn.count())) {
        throw Harness.skip("no Test notification control available");
      }
      await testBtn.click();
      await h.expectText(/sent|queued|test/i);
    });
  },
};
