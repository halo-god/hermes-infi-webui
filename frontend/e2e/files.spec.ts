import { test, expect } from "@playwright/test";

test.describe("文件管理", () => {
  test("上传文件 → 列表出现 → 删除", async ({ page }) => {
    await page.goto("/files");
    await expect(page.locator(".files-page")).toBeVisible({ timeout: 20_000 });

    const stamp = Date.now().toString().slice(-6);
    const fileName = `e2e-note-${stamp}.md`;

    // 上传按钮动态创建 file input → 用 filechooser 事件捕获
    const chooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "上传文件", exact: true }).click();
    const chooser = await chooserPromise;
    await chooser.setFiles({
      name: fileName,
      mimeType: "text/markdown",
      buffer: Buffer.from(`# E2E 测试文件\n\n由 Playwright 上传于 ${new Date().toISOString()}`),
    });

    // 列表出现该文件
    await expect(page.locator("tr", { hasText: fileName }).first()).toBeVisible({ timeout: 20_000 });

    // 删除（确认弹窗）
    const row = page.locator("tr", { hasText: fileName }).first();
    page.once("dialog", (d) => d.accept());
    await row.hover();
    await row.locator('button[title*="删除"]').first().click();
    await expect
      .poll(
        async () => (await page.locator("tr", { hasText: fileName }).count()) === 0,
        { timeout: 20_000 },
      )
      .toBe(true);
  });
});

test.describe("文件文件夹", () => {
  test("新建文件夹 → 上传文件 → 移动到文件夹", async ({ page }) => {
    await page.goto("/files");
    await expect(page.locator(".files-page")).toBeVisible({ timeout: 20_000 });

    const stamp = Date.now().toString().slice(-6);
    const folderName = `e2e-dir-${stamp}`;
    const fileName = `e2e-move-${stamp}.md`;

    // 新建文件夹
    await page.locator("button.files-btn", { hasText: "新建文件夹" }).first().click();
    await page.locator(".files-new-folder input").fill(folderName);
    await page.locator(".files-new-folder button", { hasText: "创建" }).click();
    await expect(page.locator(".files-card, .n-data-table", { hasText: folderName }).first()).toBeVisible({
      timeout: 15_000,
    });

    // 上传文件
    const chooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "上传文件", exact: true }).click();
    const chooser = await chooserPromise;
    await chooser.setFiles({
      name: fileName,
      mimeType: "text/markdown",
      buffer: Buffer.from("# 待移动文件"),
    });
    const row = page.locator("tr", { hasText: fileName }).first();
    await row.waitFor({ state: "visible", timeout: 20_000 });

    // 移动到文件夹
    await row.hover();
    await row.locator('button[title*="移动到文件夹"]').first().click();
    const moveModal = page.locator(".move-modal-body, .move-folder-list").first();
    await moveModal.waitFor({ state: "visible", timeout: 15_000 });
    await moveModal.locator(".move-folder-item", { hasText: folderName }).first().click();
    await page.locator("button", { hasText: "确认移动" }).click();

    // 刷新页面后点击文件夹名进入，验证文件在内
    await page.goto("/files");
    await expect(page.locator("tr", { hasText: folderName }).first()).toBeVisible({ timeout: 20_000 });
    await page.locator("tr", { hasText: folderName }).first().locator("span", { hasText: folderName }).first().click();
    await expect(page.locator("tr", { hasText: fileName }).first()).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("文件下载", () => {
  test("下载按钮触发文件下载", async ({ page }) => {
    await page.goto("/files");
    await expect(page.locator(".files-page")).toBeVisible({ timeout: 20_000 });

    // 需要一个文件：上传一个
    const fileName = `e2e-dl-${Date.now().toString().slice(-6)}.txt`;
    const chooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "上传文件", exact: true }).click();
    const chooser = await chooserPromise;
    await chooser.setFiles({ name: fileName, mimeType: "text/plain", buffer: Buffer.from("下载测试内容") });
    const row = page.locator("tr", { hasText: fileName }).first();
    await row.waitFor({ state: "visible", timeout: 20_000 });

    // 点击下载（download 事件）
    const downloadPromise = page.waitForEvent("download", { timeout: 20_000 });
    await row.hover();
    await row.locator('button[title="下载"]').first().click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain(fileName);
  });
});
