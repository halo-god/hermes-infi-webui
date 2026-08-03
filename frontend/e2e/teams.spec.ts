import { test, expect } from "@playwright/test";

test.describe("团队", () => {
  test("侧边栏团队入口 → 团队详情渲染（成员/统计/活动）", async ({ page }) => {
    // storageState 已登录
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });

    // 侧边栏团队列表存在
    const teamRow = page.locator(".team-row").first();
    await teamRow.waitFor({ state: "visible", timeout: 20_000 });
    const teamName = (await teamRow.innerText()).trim();

    // 点击进入团队详情
    await teamRow.click();
    await page.waitForURL(/\/teams\/.+/, { timeout: 20_000 });
    await expect(page.locator(".team-hero, .team-name").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".team-tab").first()).toBeVisible();

    // 概览：统计卡片与成员列表
    await expect(page.locator(".stat", { hasText: "成员" }).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".row-item").first()).toBeVisible();

    // 团队成员 tab
    await page.locator(".team-tab", { hasText: "成员" }).first().click();
    await expect(page.locator(".mem-avatar").first()).toBeVisible({ timeout: 15_000 });

    // 活动日志 tab
    await page.locator(".team-tab", { hasText: "活动" }).first().click();
    await expect(page.locator(".activity-item").first()).toBeVisible({ timeout: 15_000 });

    // 团队名渲染（hero 区域）
    await expect(page.locator(".team-name").first()).not.toHaveText("", { timeout: 15_000 });
  });
});

test.describe("团队知识库管理", () => {
  test("知识库 tab：列表/上传入口渲染 + 新建文件夹", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    const teamRow = page.locator(".team-row").first();
    await teamRow.waitFor({ state: "visible", timeout: 20_000 });
    await teamRow.click();
    await page.waitForURL(/\/teams\/.+/, { timeout: 20_000 });

    // 知识库 tab
    await page.locator(".team-tab", { hasText: "知识库" }).first().click();
    await expect(page.locator(".heading-serif", { hasText: "知识库" })).toBeVisible({ timeout: 15_000 });
    // 上传入口
    await expect(page.locator(".team-body button, .admin-body button", { hasText: "上传文件" }).first()).toBeVisible({ timeout: 15_000 });
    // 新建文件夹（原生 prompt 弹窗输入）
    const folderName = `e2e-kb-${Date.now().toString().slice(-6)}`;
    page.on("dialog", (d) => d.accept(folderName));
    const kbFolderBtn = page.locator(".team-body button, .admin-body button", { hasText: "新建文件夹" }).first();
    await kbFolderBtn.waitFor({ state: "visible", timeout: 15_000 });
    await kbFolderBtn.scrollIntoViewIfNeeded();
    await kbFolderBtn.click();
    await expect(page.locator(".file-row .row-title", { hasText: folderName }).first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
