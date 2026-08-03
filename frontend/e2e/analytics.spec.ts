import { test, expect } from "@playwright/test";

test.describe("用量看板", () => {
  test("统计卡片与角色分布渲染", async ({ page }) => {
    await page.goto("/analytics");
    await expect(page.locator(".analytics-page")).toBeVisible({ timeout: 20_000 });

    // 统计卡片
    await expect(page.locator(".stat-card").first()).toBeVisible({ timeout: 20_000 });
    const statCount = await page.locator(".stat-card").count();
    expect(statCount).toBeGreaterThanOrEqual(3);

    // 角色分布
    await expect(page.locator(".role-grid, .role-item").first()).toBeVisible({ timeout: 15_000 });
  });
});
