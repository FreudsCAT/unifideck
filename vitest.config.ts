import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Without a local config, vitest walks up and loads the unrelated
// vite.config.ts in the parent checkout — pin the root here.
export default defineConfig({
  resolve: {
    alias: {
      // React is peer-provided by the Steam webview (SP_REACT) and not
      // installed in node_modules — resolve it to a minimal stub so
      // steam-bridge modules can be imported under test.
      react: fileURLToPath(
        new URL("./src/test-support/react-stub.ts", import.meta.url),
      ),
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
