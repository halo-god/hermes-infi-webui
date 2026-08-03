import { test, expect } from "@playwright/test";

test.describe("后台管理 · 会话日志", () => {
  test("会话日志列表 → 详情（助手名/多轮/MD/思考过程/调用概览）", async ({ page }) => {
    // storageState 已登录（admin）
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    // 打开「会话日志」tab
    await page.locator(".admin-tabs .team-tab", { hasText: "会话日志" }).click();
    await expect(page.locator(".heading-serif", { hasText: "会话日志" })).toBeVisible();

    // 列表有数据（至少一行）
    const firstRow = page.locator(".sl-row:not(.head)").first();
    await firstRow.waitFor({ state: "visible", timeout: 30_000 });
    await expect(firstRow.locator(".sl-user-name")).not.toHaveText("");

    // 打开第一条详情
    await firstRow.locator("button", { hasText: "详情" }).click();
    await expect(page.locator(".heading-serif", { hasText: "日志详情" })).toBeVisible({ timeout: 20_000 });

    // ── 会话信息：助手名非空（profile 名如 emotion-master，或 agent id）──
    const assistant = page
      .locator(".sl-info-item")
      .filter({ has: page.locator(".sl-info-key", { hasText: /^助手$/ }) })
      .locator(".sl-info-val");
    await expect(assistant).not.toHaveText("—", { timeout: 15_000 });

    // ── 轮次卡片（支持多轮）──
    const turnCards = page.locator(".sl-turn-card");
    expect(await turnCards.count()).toBeGreaterThanOrEqual(1);
    await expect(turnCards.first().locator(".section-title")).toContainText("轮次");

    // ── Markdown 渲染容器（输入与回复均走 md-body）──
    await expect(turnCards.first().locator(".md-body").first()).toBeVisible({ timeout: 15_000 });

    // ── 思考过程（成功会话应保留）──
    const think = page.locator(".sl-think").first();
    if (await think.count()) {
      await think.locator(".sl-think-head").click();
      await expect(think.locator(".sl-think-body")).toBeVisible();
      await expect(think.locator(".sl-think-body")).not.toHaveText("");
    }

    // ── 每轮耗时徽标 ──
    await expect(turnCards.first().locator(".sl-turn-dur")).toContainText("耗时");

    // ── 调用概览（模型/工具调用列表）──
    const calls = page.locator(".sl-call-row");
    if (await calls.count()) {
      await expect(calls.first().locator(".sl-call-name")).toBeVisible();
    }
  });

  test("会话日志筛选与分页可用", async ({ page }) => {
    await page.goto("/admin");
    await page.locator(".admin-tabs .team-tab", { hasText: "会话日志" }).click();
    await expect(page.locator(".heading-serif", { hasText: "会话日志" })).toBeVisible();

    // 状态筛选循环点击（全部 → 成功）
    const statusBtn = page.locator(".users-toolbar .filter-select", { hasText: "状态" });
    await statusBtn.click();
    await expect(statusBtn).toContainText("成功");

    // 搜索框输入后回车（按首轮输入搜索）
    const search = page.locator(".users-toolbar .filter-input input");
    await search.fill("你好");
    await search.press("Enter");
    await expect(page.locator(".sl-table")).toBeVisible({ timeout: 15_000 });
  });
});
