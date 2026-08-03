import { test, expect } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("历史会话", () => {
  test("列表按日期分组渲染，搜索可过滤", async ({ page }) => {
    await page.goto("/history");
    await expect(page.locator(".hi-group").first()).toBeVisible({ timeout: 20_000 });

    const before = await page.locator(".hi-row").count();
    expect(before).toBeGreaterThan(0);

    // 按标题搜索（用第一个会话的标题）
    const firstTitle = (await page.locator(".hi-title").first().innerText()).trim();
    const search = page.locator('input[placeholder*="按标题或助手搜索"]');
    await search.fill(firstTitle.slice(0, 4));
    await page.waitForTimeout(500);
    const after = await page.locator(".hi-row").count();
    expect(after).toBeGreaterThan(0);
    expect(after).toBeLessThanOrEqual(before);
    // 结果标题包含搜索词
    await expect(page.locator(".hi-title").first()).toContainText(firstTitle.slice(0, 4));
  });

  test("删除单个会话（confirm 弹窗）", async ({ page }) => {
    await page.goto("/history");
    await expect(page.locator(".hi-row").first()).toBeVisible({ timeout: 20_000 });

    const firstRow = page.locator(".hi-row").first();
    const rowTitle = (await firstRow.locator(".hi-title").innerText()).trim();

    page.on("dialog", (d) => d.accept());
    await firstRow.locator('button[title="删除"]').click();
    // 用 API 验证删除生效（标题搜索为空即删除成功，绕过前端列表上限/渲染竞态）
    const token = adminTokenFromState();
    await expect
      .poll(
        async () => {
          const res = await page.request.get("/api/v1/conversations", {
            headers: { Authorization: `Bearer ${token}` },
            params: { q: rowTitle.slice(0, 10) },
          });
          if (res.status() !== 200) return false;
          const items = (await res.json()) as unknown[];
          return items.length === 0;
        },
        { timeout: 30_000 },
      )
      .toBe(true);
  });
});
