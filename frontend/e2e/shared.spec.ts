import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

// 共享会话是公开只读视图 —— 用未登录的干净 context 验证
test.use({ storageState: { cookies: [], origins: [] } });

test.describe("共享会话链接", () => {
  test("分享会话 → 未登录打开 /shared/:id 渲染只读视图", async ({ page }) => {
    // 前置：管理员创建会话 + 发一条消息（skipAgent 避免等待 LLM）+ 分享
    const token = adminToken();
    const convRes = await page.request.post("/api/v1/conversations", {
      headers: { Authorization: `Bearer ${token}` },
      data: { title: `E2E 分享会话-${Date.now().toString().slice(-6)}` },
    });
    expect(convRes.status(), await convRes.text()).toBe(201);
    const convId = (await convRes.json()).id as string;

    const sendRes = await page.request.post(`/api/v1/conversations/${convId}/messages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { text: "这是一条用于分享测试的消息内容", skip_agent: true },
    });
    expect(sendRes.status(), await sendRes.text()).toBe(200);

    const shareRes = await page.request.post(`/api/v1/conversations/${convId}/share`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(shareRes.status(), await shareRes.text()).toBe(200);
    const shareUrl = (await shareRes.json()).share_url as string;

    // 未登录打开分享链接（公开只读，无需登录）
    await page.goto(shareUrl);
    await expect(page.locator(".shared-page")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".shared-title")).toContainText("E2E 分享会话", { timeout: 15_000 });
    // 消息内容渲染
    await expect(page.locator(".shared-list, .shared-msg, .msg").first()).toContainText(
      "这是一条用于分享测试的消息内容",
      { timeout: 15_000 },
    );
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
