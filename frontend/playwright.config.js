import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "e2e",
  timeout: 45000,
  retries: 0,
  workers: 1,
  webServer: process.env.E2E_FIXTURES
    ? {
        command: "../.venv/bin/python ../scripts/serve_e2e.py",
        url: "http://127.0.0.1:18001/health/live",
        timeout: 30000,
        reuseExistingServer: false,
      }
    : undefined,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:18000",
    headless: true,
    channel: "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "only-on-failure",
  },
});
