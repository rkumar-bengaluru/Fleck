import { defineConfig } from "@playwright/test";

// Runs against the live stack (`make up`): web on 5173, api, gsim.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://localhost:5173", headless: true },
});
