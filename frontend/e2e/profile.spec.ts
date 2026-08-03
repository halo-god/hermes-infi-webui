import { test, expect } from "@playwright/test";

test.describe("个人设置", () => {
  test("资料页渲染（头像/基本资料/记忆区）", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator(".team-name, .admin-hero").first()).toBeVisible({ timeout: 20_000 });

    // 基本资料表单
    await expect(page.locator(".section-title", { hasText: "基本资料" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('input.cfg-input[value], input.cfg-input').first()).toBeVisible();

    // 角色标识
    await expect(page.locator(".role-pill").first()).toBeVisible({ timeout: 15_000 });

    // 代理记忆 tab
    await page.locator(".team-tab", { hasText: "代理记忆" }).click();
    await expect(page.locator(".section-title", { hasText: /记忆/ }).first()).toBeVisible({ timeout: 15_000 });
  });
});
