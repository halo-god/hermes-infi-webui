import { test, expect } from "@playwright/test";

test.describe("后台管理 · 用户管理", () => {
  test("新建用户 → 列表出现 → 搜索定位", async ({ page }) => {
    // storageState 已登录（admin）
    await page.goto("/admin");
    await expect(page.locator(".admin-hero")).toBeVisible({ timeout: 20_000 });

    // 用户管理 tab
    await page.locator(".admin-tabs .team-tab", { hasText: "用户管理" }).click();
    await expect(page.locator(".ut-row.head")).toBeVisible({ timeout: 15_000 });

    const stamp = Date.now().toString().slice(-6);
    const email = `e2e-admin-${stamp}@hermes.io`;

    // 打开创建表单并填写（表单卡片无标题，直接按 placeholder 定位）
    await page.locator("button", { hasText: "新建用户" }).click();
    await page.locator('input[placeholder="姓名"]').fill("E2E 测试员");
    await page.locator('input[placeholder="邮箱"]').fill(email);
    await page.locator('input[placeholder*="初始密码"]').fill("E2ePass@2026");
    await page.locator('input[placeholder="部门"]').fill("测试部");
    await page.locator("select.cfg-input").first().selectOption({ label: "成员" });
    await page.locator("button", { hasText: "创建" }).click();

    // 列表出现新用户（搜索定位）
    const search = page.locator('.users-toolbar input[placeholder*="搜索"]');
    await search.fill(email);
    await search.press("Enter");
    await expect(page.locator(".ut-row", { hasText: email }).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".ut-row", { hasText: email }).first()).toContainText("E2E 测试员");
  });

  test("用户列表渲染与状态筛选", async ({ page }) => {
    // storageState 已登录（admin）
    await page.goto("/admin");
    await page.locator(".admin-tabs .team-tab", { hasText: "用户管理" }).click();
    await expect(page.locator(".ut-row.head")).toBeVisible({ timeout: 15_000 });

    const total = await page.locator(".ut-row:not(.head)").count();
    expect(total).toBeGreaterThan(0);

    // 状态筛选：全部 → 激活
    const statusBtn = page.locator(".users-toolbar .filter-select", { hasText: "状态" });
    await statusBtn.click();
    await expect(statusBtn).toContainText("激活");
    const active = await page.locator(".ut-row:not(.head)").count();
    expect(active).toBeLessThanOrEqual(total);
  });
});
