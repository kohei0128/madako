import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.MADAKO_E2E_PORT ?? "8000");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `../.venv/bin/madako build-sample --source e2e/data/profiles.json --output-dir .e2e-data && ../.venv/bin/madako serve --storage-dir .e2e-data --port ${port}`,
      port,
      reuseExistingServer: false,
    },
  ],
});
