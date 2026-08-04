import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("引用知识库", () => {
  test("团队知识库 → 群聊引用知识条目发送", async ({ page }) => {
    // 前置：创建团队 + 知识条目（知识库引用需会话绑定团队）
    const token = adminToken();
    const teamName = `E2E 知识库团队-${Date.now().toString().slice(-6)}`;
    const teamRes = await page.request.post("/api/v1/teams", {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: teamName },
    });
    expect(teamRes.status(), await teamRes.text()).toBe(201);
    const teamId = (await teamRes.json()).id as string;

    const kbName = "E2E 产品手册";
    const kbRes = await page.request.post(`/api/v1/teams/${teamId}/knowledge`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: kbName, kind: "md", content: "# 产品手册\n\n版本 1.0 功能说明" },
    });
    expect(kbRes.status(), await kbRes.text()).toBe(201);

    // UI：创建群聊（选该团队）→ 附件 → 引用知识库 → 勾选 → 发送
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    await page.locator("button[title='创建群聊']").click();
    const teamCard = page.locator(".team-card", { hasText: teamName }).first();
    await teamCard.waitFor({ state: "visible", timeout: 20_000 });
    await teamCard.click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    // 引用知识库（等待知识加载完成 —— knowledgeItems 来自团队）
    await page.locator('.composer-tool[title="附件"]').click();
    const kbMenuItem = page.locator(".menu-item", { hasText: "引用知识库" });
    await kbMenuItem.waitFor({ state: "visible", timeout: 15_000 });
    await expect(kbMenuItem).toBeEnabled({ timeout: 20_000 });
    await kbMenuItem.click();

    const kbPicker = page.locator(".menu", { hasText: "选择知识库条目" }).last();
    await kbPicker.waitFor({ state: "visible", timeout: 15_000 });
    const kbItem = kbPicker.locator(".menu-item", { hasText: kbName }).first();
    await kbItem.waitFor({ state: "visible", timeout: 15_000 });
    await kbItem.click();

    // 发送消息
    const composer = page.locator(".composer-input");
    await composer.type("请基于知识库回答");
    await composer.press("Enter");

    // 用户消息出现且带知识引用 chip
    const userMsg = page.locator(".msg.user").last();
    await userMsg.waitFor({ state: "visible", timeout: 15_000 });
    await expect(userMsg.locator(".knowledge-chip").first()).toBeVisible({ timeout: 10_000 });
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
