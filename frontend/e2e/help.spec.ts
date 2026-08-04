import { test, expect } from "@playwright/test";

test.describe("帮助中心", () => {
  test("功能模块 tab 切换与 FAQ 渲染", async ({ page }) => {
    await page.goto("/help");
    await expect(page.locator(".admin-hero").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".admin-tabs .team-tab").first()).toBeVisible();

    // 常见问题 tab
    await page.locator(".admin-tabs .team-tab", { hasText: "常见问题" }).click();
    await expect(page.locator(".help-feature-grid, .help-faq, .section-card").first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
