import { test, expect } from "@playwright/test";

test.describe("后台管理 · 概览与助手", () => {
  test("概览统计卡片与角色分布", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    // 概览 tab（默认）
    await expect(page.locator(".stat-grid").first()).toBeVisible({ timeout: 20_000 });
    const stats = await page.locator(".stat-grid .stat, .stat-grid > div").count();
    expect(stats).toBeGreaterThanOrEqual(4);
  });

  test("助手管理 tab：助手列表渲染", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    await page.locator(".admin-tabs .team-tab", { hasText: "助手管理" }).click();
    await expect(page.locator(".heading-serif", { hasText: "助手管理" })).toBeVisible({ timeout: 15_000 });
    // 助手列表或空态渲染（profiles 来自后端）
    await expect(page.locator("button", { hasText: "新建助手" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".empty-state-lg, div[style*='border-bottom']").first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test("MCP 服务器 tab：列表渲染（stdio/http 兼容）", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    await page.locator(".admin-tabs .team-tab", { hasText: "MCP 服务器" }).click();
    await expect(page.locator(".heading-serif", { hasText: "MCP 服务器" })).toBeVisible({ timeout: 15_000 });
    // 列表容器渲染（可能为空态或条目）
    await expect(page.locator(".mcp-list, .section-card, .empty-state-lg").first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test("系统设置 tab：配置表单渲染", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    await page.locator(".admin-tabs .team-tab", { hasText: "系统设置" }).click();
    await expect(page.locator(".heading-serif", { hasText: "系统设置" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".cfg-input, .cfg-grid").first()).toBeVisible({ timeout: 20_000 });
  });
});
