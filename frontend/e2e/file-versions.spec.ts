import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("文件版本", () => {
  test("上传 → 修改两次 → 版本历史预览", async ({ page }) => {
    // 前置：创建会话 + 上传文件 + PATCH 两次（产生 v1/v2/v3）
    const token = adminToken();
    const convRes = await page.request.post("/api/v1/conversations", {
      headers: { Authorization: `Bearer ${token}` },
      data: { title: `E2E 版本测试-${Date.now().toString().slice(-6)}` },
    });
    expect(convRes.status(), await convRes.text()).toBe(201);
    const convId = (await convRes.json()).id as string;

    const up = await page.request.post(`/api/v1/conversations/${convId}/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: "versioned.md", mimeType: "text/markdown", buffer: Buffer.from("版本 1 内容") },
      },
    });
    expect(up.status(), await up.text()).toBe(201);
    const fileId = (await up.json()).id as string;

    const patch1 = await page.request.patch(`/api/v1/conversations/${convId}/files/${fileId}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { content: "版本 2 内容" },
    });
    expect(patch1.status(), await patch1.text()).toBe(200);
    const patch2 = await page.request.patch(`/api/v1/conversations/${convId}/files/${fileId}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { content: "版本 3 内容" },
    });
    expect(patch2.status(), await patch2.text()).toBe(200);

    // UI：打开会话（侧边栏点击）→ 工作区面板 → 版本历史
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    const convoRow = page.locator(".convo-title", { hasText: "E2E 版本测试" }).first();
    await convoRow.waitFor({ state: "visible", timeout: 20_000 });
    await convoRow.click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    // 展开工作区面板（thread-action 含文件数）
    const wsToggle = page.locator("button.thread-action", { hasText: "工作区" });
    await wsToggle.waitFor({ state: "visible", timeout: 20_000 });
    await wsToggle.click();

    // 打开工作区面板中的文件
    const fileNode = page.locator(".ws-file", { hasText: "versioned.md" }).first();
    await fileNode.waitFor({ state: "visible", timeout: 20_000 });
    await fileNode.click();

    // 版本历史按钮
    const versionBtn = page.locator('button[title="版本历史"]');
    await versionBtn.waitFor({ state: "visible", timeout: 15_000 });
    await versionBtn.click();

    // 版本列表：v3/v2/v1 三条
    const versions = page.locator(".ws-versions .ws-ver-item");
    await versions.first().waitFor({ state: "visible", timeout: 15_000 });
    const count = await versions.count();
    expect(count).toBeGreaterThanOrEqual(2);

    // 点击 v1 的「预览」查看旧内容
    const v1 = versions.filter({ hasText: "v1" }).first();
    await v1.locator("button", { hasText: "预览" }).click();
    await expect(page.locator(".ws-ver-preview-banner")).toContainText("预览 v1", { timeout: 15_000 });
    // 预览默认进 Diff 视图（showPreviewDiff 默认 true）——点「Diff 对比」切回内容视图
    await page.locator(".ws-ver-preview-banner button", { hasText: "Diff 对比" }).click();
    await expect(page.locator(".md-preview, .ws-content, pre").first()).toContainText("版本 1 内容", {
      timeout: 15_000,
    });
  });
});

test.describe("文件版本恢复", () => {
  test("恢复 v1 后当前内容回退且版本号递增", async ({ page }) => {
    // 前置：创建会话 + 上传 + PATCH 两次（v1 历史 + 当前 v3）
    const token = adminToken();
    const convRes = await page.request.post("/api/v1/conversations", {
      headers: { Authorization: `Bearer ${token}` },
      data: { title: `E2E 版本恢复-${Date.now().toString().slice(-6)}` },
    });
    expect(convRes.status(), await convRes.text()).toBe(201);
    const convId = (await convRes.json()).id as string;
    const up = await page.request.post(`/api/v1/conversations/${convId}/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: "restore.md", mimeType: "text/markdown", buffer: Buffer.from("恢复测试 v1") },
      },
    });
    expect(up.status(), await up.text()).toBe(201);
    const fileId = (await up.json()).id as string;
    for (const content of ["恢复测试 v2", "恢复测试 v3"]) {
      const r = await page.request.patch(`/api/v1/conversations/${convId}/files/${fileId}`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { content },
      });
      expect(r.status(), await r.text()).toBe(200);
    }

    // UI：打开会话 → 工作区 → 版本历史 → 恢复 v1
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    await page.locator(".convo-title", { hasText: "E2E 版本恢复" }).first().click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });
    await page.locator("button.thread-action", { hasText: "工作区" }).waitFor({ state: "visible", timeout: 20_000 });
    await page.locator("button.thread-action", { hasText: "工作区" }).click();
    await page.locator(".ws-file", { hasText: "restore.md" }).first().click();
    await page.locator('button[title="版本历史"]').click();

    const versions = page.locator(".ws-versions .ws-ver-item");
    await versions.first().waitFor({ state: "visible", timeout: 15_000 });
    const beforeCount = await versions.count();

    // 恢复 v1（版本历史最后一条）
    const v1 = versions.filter({ hasText: "v1" }).first();
    await v1.locator("button", { hasText: "恢复" }).click();

    // 恢复后：当前内容回退为 v1 文本（restoreVer 直接更新内容区）
    await expect(page.locator(".md-preview, .ws-content, pre").first()).toContainText("恢复测试 v1", {
      timeout: 15_000,
    });
    // 重新打开版本历史：恢复产生新版本（v4），版本数 +1
    await page.locator('button[title="版本历史"]').click();
    await page.locator(".ws-versions .ws-ver-item").first().waitFor({ state: "visible", timeout: 15_000 });
    const afterCount = await page.locator(".ws-versions .ws-ver-item").count();
    expect(afterCount).toBeGreaterThan(beforeCount);
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
