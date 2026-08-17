import { test, expect } from "@playwright/test";

test.describe("反馈", () => {
  test("提交反馈表单成功", async ({ page }) => {
    await page.goto("/feedback");
    await expect(page.locator(".admin-title, .admin-hero").first()).toBeVisible({ timeout: 20_000 });

    const stamp = Date.now().toString().slice(-6);
    // 表单默认隐藏，先点「提交反馈」展开（toolbar 主按钮）
    const submitBtn = page.locator("button.btn.primary", { hasText: "提交反馈" }).first();
    await submitBtn.waitFor({ state: "visible", timeout: 20_000 });
    await submitBtn.scrollIntoViewIfNeeded();
    await submitBtn.click();
    await page.locator('input[placeholder*="反馈标题"]').fill(`E2E 反馈-${stamp}`);
    await page.locator('textarea[placeholder*="详细描述"]').fill("这是 Playwright 自动化测试提交的反馈内容。");
    // 表单内分类是 select（.fb-cat-pill 属于反馈列表，依赖历史数据，不能用作表单选择）
    await page.locator(".section-card .fb-select").first().selectOption("bug");
    // 表单内的提交按钮（文本恰为「提交」）
    await page.locator("button.btn.primary", { hasText: /^提交$/ }).first().click();

    // 提交成功提示（toast）
    await expect(page.locator(".toast-stack .toast-item").first()).toBeVisible({ timeout: 15_000 });
    // 提交成功后表单自动收起
    await expect(page.locator('input[placeholder*="反馈标题"]')).toHaveCount(0, { timeout: 15_000 });
  });
});
