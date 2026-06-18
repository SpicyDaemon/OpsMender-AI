import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Give individual tests headroom beyond the 5s asyncUtilTimeout so a slow
    // render under parallel load can't trip vitest's own per-test timeout.
    testTimeout: 15000,
  },
});
