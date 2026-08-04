import { defineConfig } from "@playwright/test";

/**
 * E2E tests run against the real full stack (dev servers):
 *   - frontend:  http://localhost:5173 (vite dev)
 *   - backend:   http://localhost:8001 (uvicorn, proxied by vite at /api)
 *
 * Start them first:
 *   cd backend  && ./start-agent.sh  (or existing runner)
 *   ./start-api.sh
 *   ./start-web.sh
 *
 * Single worker: the tests share the real backend and must not race on
 * rate limits / conversation data.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: "http://localhost:5173",
    storageState: "./e2e/.auth/state.json",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
