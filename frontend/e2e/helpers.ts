import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

export const ADMIN_EMAIL = "admin@hermes.io";
export const ADMIN_PASSWORD = "Hermes@2026";

/** UI login through the real login page. */
export async function login(page: Page, username = ADMIN_EMAIL, password = ADMIN_PASSWORD) {
  await page.goto("/login");
  await page.locator(".login-input").first().fill(username);
  await page.locator(".login-input").nth(1).fill(password);
  await page.locator(".login-submit").click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 20_000 });
  // Sidebar must render the user before we consider the session usable.
  await expect(page.locator(".side")).toBeVisible({ timeout: 15_000 });
}

/** Create a member user via the admin API (bypasses UI user management). */
export async function createMemberUser(
  page: Page,
  email: string,
  password = "Member@2026",
): Promise<void> {
  const token = await adminToken(page);
  const res = await page.request.post("/api/v1/admin/users", {
    headers: { Authorization: `Bearer ${token}` },
    data: { email, name: "E2E 成员", password, role: "member" },
  });
  expect(res.status(), await res.text()).toBe(201);
}

async function adminToken(page: Page): Promise<string> {
  const res = await page.request.post("/api/v1/auth/login", {
    data: { method: "local", username: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  });
  expect(res.status()).toBe(200);
  return (await res.json()).access_token as string;
}

/** Read the admin access token from the storageState written by globalSetup —
 * avoids extra API logins (per-IP login rate limit is 10/min). */
export function adminTokenFromState(): string {
  const state = JSON.parse(
    readFileSync("e2e/.auth/state.json", "utf-8"),
  ) as { origins: { localStorage: { name: string; value: string }[] }[] };
  const item = state.origins[0].localStorage.find((x) => x.name === "hermes.access");
  if (!item) throw new Error("storageState missing hermes.access — run globalSetup first");
  return item.value;
}
