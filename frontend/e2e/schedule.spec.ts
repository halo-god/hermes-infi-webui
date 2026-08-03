import { test, expect } from "@playwright/test";

test.describe("定时任务", () => {
  test("新建任务 → 列表出现 → 删除", async ({ page }) => {
    await page.goto("/schedule");
    await expect(page.locator(".admin-hero").first()).toBeVisible({ timeout: 20_000 });

    const stamp = Date.now().toString().slice(-6);
    const taskName = `E2E 周报-${stamp}`;

    // 新建任务
    await page.locator("button", { hasText: "新建任务" }).click();
    const form = page.locator(".section-card", { hasText: "新建定时任务" }).first();
    await form.locator('input[placeholder*="每周五"]').fill(taskName);
    await form.locator("textarea.cfg-input").fill("请生成本周工作周报草稿");
    await form.locator("button", { hasText: "保存" }).click();

    // 列表出现新任务
    await expect(page.locator(".sched-name", { hasText: taskName }).first()).toBeVisible({
      timeout: 20_000,
    });

    // 删除（确认弹窗；actions 中 title="删除" 的按钮）
    const row = page.locator(".sched-body", { hasText: taskName }).locator("..");
    page.once("dialog", (d) => d.accept());
    await row.locator('button[title="删除"]').click();
    await expect
      .poll(
        async () => (await page.locator(".sched-name", { hasText: taskName }).count()) === 0,
        { timeout: 20_000 },
      )
      .toBe(true);
  });
});
