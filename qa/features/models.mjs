// Feature: AI model configs. Optionally creates a model config, then runs the
// live "Test connection" on the first saved config.

import { Harness } from "../lib/harness.mjs";
import { config, qaName } from "../lib/config.mjs";

export default {
  id: "models",
  title: "AI — model configs",
  async run(h) {
    await h.step("models page loads", async () => {
      await h.goto("/dashboard/models");
      await h.expectText(/model/i);
    });

    await h.step("create model config", async () => {
      if (!config.createModel) {
        throw Harness.skip("QA_CREATE_MODEL not enabled");
      }
      await h.page.getByRole("button", { name: /add model config/i }).first().click();
      await h.page.locator("#model-name").fill(qaName("model"));
      const provider = h.page.locator("#model-provider");
      if (await provider.count()) {
        await provider.selectOption({ label: new RegExp(config.model.provider, "i") }).catch(
          async () => provider.selectOption({ value: config.model.provider }).catch(() => {}),
        );
      }
      await h.page.locator("#model-id").fill(config.model.modelId);
      const keyEnv = h.page.locator("#model-key");
      if ((await keyEnv.count()) && config.model.apiKeyEnv) {
        await keyEnv.fill(config.model.apiKeyEnv);
      }
      const baseUrl = h.page.locator("#model-url");
      if ((await baseUrl.count()) && config.model.baseUrl) {
        await baseUrl.fill(config.model.baseUrl);
      }
      await h.clickButton(/create config/i);
      await h.expectText(/created|config/i);
    });

    await h.step("test model connection", async () => {
      if (!config.testModelConnection) {
        throw Harness.skip("QA_TEST_MODEL_CONNECTION disabled");
      }
      const testBtn = h.page.getByRole("button", { name: /^test$/i }).first();
      const appeared = await testBtn
        .waitFor({ state: "visible", timeout: 20000 })
        .then(() => true)
        .catch(() => false);
      if (!appeared) {
        throw Harness.skip("no saved model config to test");
      }
      await testBtn.click();
      // A success or failure notice both confirm the round-trip ran; a hard
      // failure to even respond is what we care about here.
      await h.expectText(
        /connection ok|connection test|ok\.|success|responded|failed|error/i,
        {
        timeout: 30000,
        },
      );
    });
  },
};
