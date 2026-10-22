import { defineConfig, devices } from "@playwright/test";

/**
 * E2E de los módulos legales.
 *
 * Requisitos locales:
 *   docker compose up -d api web
 *   cd tests/e2e && npm install && npx playwright install chromium
 *   npm test
 *
 * Credenciales: E2E_EMAIL / E2E_PASSWORD. Si no se definen, el fixture lee
 * BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD del .env de la raíz.
 * Nunca se hardcodean secretos en este archivo.
 */
const WEB = process.env.E2E_BASE_URL || "http://localhost:3000";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  globalSetup: require.resolve("./global-setup"),
  reporter: [
    ["list"],
    ["html", { outputFolder: "artifacts/html", open: "never" }],
    ["json", { outputFile: "artifacts/results.json" }],
  ],
  use: {
    baseURL: WEB,
    storageState: "artifacts/storage-state.json",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  outputDir: "artifacts/test-results",
  projects: [
    {
      name: "chromium-360",
      use: { ...devices["Pixel 5"], viewport: { width: 360, height: 800 } },
    },
    {
      name: "chromium-768",
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 900 } },
    },
    {
      name: "chromium-1024",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1024, height: 900 } },
    },
    {
      name: "chromium-1440",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
});
