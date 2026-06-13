import { defineConfig } from "vitest/config";

// Without a local config, vitest walks up and loads the unrelated
// vite.config.ts in the parent checkout — pin the root here.
export default defineConfig({
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
