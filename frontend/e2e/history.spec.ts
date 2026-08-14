import { test, expect } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("历史会话", () => {
  // 删除/列表偶发受真实后端抖动影响，允许重试一次
  test.describe.configure({ retries: 1 });
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

    // 删除前：API 定位该标题的会话 id（标题搜索验证删除在大量同名 E2E
    // 会话堆积时不可靠——搜索分页/同名会互相干扰）
    const token = adminTokenFromState();
    const searchRes = await page.request.get("/api/v1/conversations", {
      headers: { Authorization: `Bearer ${token}` },
      params: { q: rowTitle },
    });
    expect(searchRes.status()).toBe(200);
    const items = (await searchRes.json()) as { id: string; title?: string }[];
    const target = items.find((c) => c.title === rowTitle);
    expect(target, `会话「${rowTitle}」应存在于 API`).toBeTruthy();

    page.on("dialog", (d) => d.accept());
    await firstRow.locator('button[title="删除"]').click();
    // 验证：该会话 ID 查询变为 404 即删除成功（不依赖标题唯一性）
    await expect
      .poll(
        async () => {
          const res = await page.request.get(`/api/v1/conversations/${target!.id}`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          return res.status() === 404;
        },
        { timeout: 30_000 },
      )
      .toBe(true);
  });
});
