import { request as pwRequest } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { ADMIN_EMAIL, ADMIN_PASSWORD, E2E_API_URL, E2E_BASE_URL } from "./helpers";
import { cleanupE2EData } from "./cleanup";

/**
 * One admin login per suite run → storageState for all tests that don't
 * exercise auth itself. Avoids tripping the per-IP login rate limit
 * (10/min) when the suite logs in repeatedly.
 */
export default async function globalSetup(): Promise<void> {
  const ctx = await pwRequest.newContext({ baseURL: E2E_API_URL });
  const res = await ctx.post("/api/v1/auth/login", {
    data: { method: "local", username: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  if (res.status() !== 200) {
    const text = await res.text();
    await ctx.dispose();
    throw new Error(`E2E admin login failed (${res.status()}): ${text}`);
  }
  const body = (await res.json()) as { access_token: string; refresh_token: string };
  await ctx.dispose();

  // Wipe leftovers from previous runs (E2E-prefixed teams + scheduled tasks)
  // so `.first()` selectors don't hit stale duplicates.
  await cleanupE2EData(E2E_API_URL, body.access_token);

  mkdirSync("e2e/.auth", { recursive: true });
  writeFileSync(
    "e2e/.auth/state.json",
    JSON.stringify({
      cookies: [],
      origins: [
        {
          origin: E2E_BASE_URL,
          localStorage: [
            { name: "hermes.access", value: body.access_token },
            { name: "hermes.refresh", value: body.refresh_token },
          ],
        },
      ],
    }),
  );
}
