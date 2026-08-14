import { defineConfig } from "@playwright/test";

/**
 * E2E tests run against the real full stack:
 *   - frontend:  http://localhost:5173 (vite dev/preview)
 *   - backend:   http://localhost:8001 (uvicorn, proxied by vite at /api)
 *
 * Both endpoints are overridable via E2E_BASE_URL / E2E_API_URL (CI uses
 * vite preview + a clean uvicorn). Start the stack locally first:
 *   cd backend  && ./start-agent.sh  (or existing runner)
 *   ./start-api.sh
 *   ./start-web.sh
 *
 * Single worker: the tests share the real backend and must not race on
 * rate limits / conversation data.
 */
const BASE_URL = process.env.E2E_BASE_URL || "http://localhost:5173";

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
    baseURL: BASE_URL,
    storageState: "./e2e/.auth/state.json",
    // Headless Chromium defaults to en-US; the app detects the UI language
    // from navigator.language, so zh-CN keeps Chinese-text assertions valid.
    locale: "zh-CN",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
