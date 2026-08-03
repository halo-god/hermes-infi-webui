import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("团队邀请链接", () => {
  test("生成邀请 token → 打开 /i/:handle/:token 加入团队", async ({ page }) => {
    // storageState 已登录（admin）—— 邀请加入需要登录态
    // 前置：创建团队 + 邀请 token
    const token = adminToken();
    const teamName = `E2E 邀请团队-${Date.now().toString().slice(-6)}`;
    const teamRes = await page.request.post("/api/v1/teams", {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: teamName, handle: `e2e-invite-${Date.now().toString().slice(-6)}` },
    });
    expect(teamRes.status(), await teamRes.text()).toBe(201);
    const team = (await teamRes.json()) as { id: string; handle: string };

    const invRes = await page.request.post(`/api/v1/teams/${team.id}/invite-token`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { role: "member", expires_days: 7 },
    });
    expect(invRes.status(), await invRes.text()).toBe(200);
    const inviteToken = (await invRes.json()).token as string;

    // 打开邀请链接：已是团队成员/加入成功 → 前往团队主页
    await page.goto(`/i/${team.handle}/${inviteToken}`);
    await expect(page.locator("button", { hasText: "前往团队主页" })).toBeVisible({ timeout: 20_000 });
    await page.locator("button", { hasText: "前往团队主页" }).click();
    await page.waitForURL(/\/teams\/.+/, { timeout: 20_000 });
    await expect(page.locator(".team-hero, .team-name").first()).toBeVisible({ timeout: 20_000 });
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
