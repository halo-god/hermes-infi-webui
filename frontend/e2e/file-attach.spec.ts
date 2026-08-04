import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("引用文件", () => {
  test("附件菜单引用文件 → 发送消息带文件引用", async ({ page }) => {
    // 前置：上传一个 standalone 文件
    const token = adminToken();
    const fileName = `e2e-ref-${Date.now().toString().slice(-6)}.md`;
    const up = await page.request.post("/api/v1/files/upload", {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: fileName,
          mimeType: "text/markdown",
          buffer: Buffer.from("# 引用测试文件\n\n内容 A"),
        },
      },
    });
    expect(up.status(), await up.text()).toBe(201);

    // UI：新会话 → 附件 → 引用文件管理文件 → 勾选 → 确定
    await page.goto("/");
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });
    await page.locator('.composer-tool[title="附件"]').click();
    await page.locator(".menu-item", { hasText: "引用文件管理文件" }).click();

    const filePicker = page.locator(".menu", { hasText: "确定" }).last();
    await filePicker.waitFor({ state: "visible", timeout: 15_000 });
    const fileItem = filePicker.locator(".menu-item", { hasText: fileName }).first();
    await fileItem.waitFor({ state: "visible", timeout: 15_000 });
    await fileItem.click();
    await filePicker.locator("button", { hasText: /确定/ }).click();

    // 发送消息
    const composer = page.locator(".composer-input");
    await composer.type("请查看引用的文件");
    await composer.press("Enter");

    // 用户消息出现且带文件引用 chip
    const userMsg = page.locator(".msg.user").last();
    await userMsg.waitFor({ state: "visible", timeout: 15_000 });
    await expect(userMsg.locator(".msg-file-chip, .msg-files").first()).toBeVisible({ timeout: 10_000 });
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
