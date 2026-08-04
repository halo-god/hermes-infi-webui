import { test, expect, type Page } from "@playwright/test";

/** 发送一条消息并等待助手流式回复完成。 */
async function sendMessage(page: Page, text: string) {
  const composer = page.locator(".composer-input");
  await composer.waitFor({ state: "visible", timeout: 20_000 });
  await composer.fill(text);
  await composer.press("Enter");
  // 用户消息气泡出现
  await expect(page.locator(".msg.user").last()).toBeVisible({ timeout: 15_000 });
  // 助手回复出现（流式渲染，先有气泡后有内容）
  const agentMsg = page.locator(".msg.agent").last();
  await agentMsg.waitFor({ state: "visible", timeout: 15_000 });
  // 等待流式结束：回复文本非空且停止变化（给足真实 LLM 时间）
  await expect
    .poll(
      async () => (await agentMsg.locator(".msg-bubble, .md-body").first().innerText()).trim().length,
      { timeout: 150_000, intervals: [2_000] },
    )
    .toBeGreaterThan(0);
  // 等「生成中」标识消失（流结束）
  await expect(page.locator(".convo-live-label")).toHaveCount(0, { timeout: 150_000 });
  return agentMsg;
}

test.describe("聊天主流程", () => {
  test("新建会话并发送消息，助手流式回复", async ({ page }) => {
    // storageState 已登录，直接进入工作区
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });

    // 新建会话：点击侧边栏「首页」
    await page.locator(".side-row", { hasText: "首页" }).first().click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    const text = "你好，请用一句话介绍你自己";
    await sendMessage(page, text);

    // 用户输入已上屏
    await expect(page.locator(".msg.user").last()).toContainText("你好", { timeout: 15_000 });
    // 侧边栏出现新会话标题（自动取首条消息）
    await expect(page.locator(".side .convo-item, .side .side-row").first()).toBeVisible();
  });

  test("多轮对话：追问后助手继续回复", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });

    // 新建会话
    await page.locator(".side-row", { hasText: "首页" }).first().click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    await sendMessage(page, "你好");
    await sendMessage(page, "再介绍一下你能做什么？");

    const agentCount = await page.locator(".msg.agent").count();
    expect(agentCount).toBeGreaterThanOrEqual(2);
    // 第二轮回复有内容
    const lastReply = await page.locator(".msg.agent").last().locator(".msg-bubble, .md-body").first().innerText();
    expect(lastReply.trim().length).toBeGreaterThan(0);
  });
});
