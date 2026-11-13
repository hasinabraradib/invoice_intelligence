import { defineConfig, devices } from "@playwright/test";

// Smoke test against the running stack (make up). It skips itself if the stack is down.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  use: {
    baseURL: process.env.E2E_WEB_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
