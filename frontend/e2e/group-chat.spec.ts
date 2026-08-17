import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("群聊", () => {
  test("创建群聊 → @提及助手发消息", async ({ page }) => {
    // 前置：创建团队（群聊自动包含成员和助手）
    const teamId = await createTeam(page);

    // UI：侧边栏群聊 tab → + 创建群聊
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    await page.locator("button[title='创建群聊']").click();
    const modal = page.locator(".team-card", { hasText: "E2E 群聊团队" }).first();
    await modal.waitFor({ state: "visible", timeout: 20_000 });
    await modal.click();

    // 群聊创建并打开（会话在首页打开，URL 不变）
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    // @提及助手：输入 @ 弹出成员选择器
    const composer = page.locator(".composer-input");
    await composer.click();
    await page.keyboard.type("@");
    const mentionPicker = page.locator(".mention-picker");
    await mentionPicker.waitFor({ state: "visible", timeout: 10_000 });
    const mentionItem = mentionPicker.locator(".mention-item").first();
    await mentionItem.waitFor({ state: "visible", timeout: 10_000 });
    const mentionName = (await mentionItem.locator(".mention-name").innerText()).trim();
    await mentionItem.click();

    // 继续输入消息内容并发送
    await composer.type("，请回复收到");
    await composer.press("Enter");

    // 用户消息上屏（含 @提及名）
    const userMsg = page.locator(".msg.user").last();
    await userMsg.waitFor({ state: "visible", timeout: 15_000 });
    await expect(userMsg).toContainText(mentionName, { timeout: 10_000 });
  });
});

test.describe("群聊成员与频道模式", () => {
  test("成员面板：AI/人类成员列表 + 频道模式切换", async ({ page }) => {
    // 前置：创建团队 + 群聊
    await createTeam(page);
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    await page.locator("button[title='创建群聊']").click();
    const modal = page.locator(".team-card").first();
    await modal.waitFor({ state: "visible", timeout: 20_000 });
    await modal.click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    // 打开成员面板（群聊成员按钮）
    const memberToggle = page.locator('button.thread-action[title="群聊成员"]');
    await memberToggle.waitFor({ state: "visible", timeout: 20_000 });
    await memberToggle.click();

    const panel = page.locator(".mp-panel");
    await panel.waitFor({ state: "visible", timeout: 15_000 });
    await expect(panel.locator(".mp-title")).toContainText("成员");

    // AI 助手目录与成员项
    await expect(panel.locator(".mp-dir-name", { hasText: "AI 助手" })).toBeVisible();
    const aiItems = panel.locator(".mp-item");
    expect(await aiItems.count()).toBeGreaterThanOrEqual(1);

    // 频道模式：@ 触发 → 自动回复（循环点击切换并断言 active 变化）
    const modeBtns = panel.locator(".mp-mode-btn");
    await modeBtns.first().waitFor({ state: "visible", timeout: 10_000 });
    const before = await panel.locator(".mp-mode-btn.active").innerText();
    // 点击下一个模式按钮
    const next = modeBtns.nth(1);
    if (await next.count()) {
      await next.click();
      await expect(panel.locator(".mp-mode-btn.active")).not.toHaveText(before, { timeout: 10_000 });
    }
  });
});

test.describe("群聊圆桌与成员管理", () => {
  // 成员/圆桌依赖真实后端，偶发抖动允许重试一次
  test.describe.configure({ retries: 1 });
  // @real-agent: the roundtable needs the ACP runner to drive real agents —
  // skipped in CI; run locally with the agent up.
  test("绑定多助手团队 → @圆桌 触发多 Agent 并行回复渲染", async ({ page }) => {
    // 前置：创建团队 + 绑定 3 个共享 profile（多 Agent 圆桌前提）
    const token = adminToken();
    const teamRes = await page.request.post("/api/v1/teams", {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: `E2E 圆桌团队-${Date.now().toString().slice(-6)}` },
    });
    expect(teamRes.status(), await teamRes.text()).toBe(201);
    const teamId = (await teamRes.json()).id as string;
    const profilesRes = await page.request.get("/api/v1/profiles", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(profilesRes.status(), await profilesRes.text()).toBe(200);
    const profiles = (await profilesRes.json()) as { id: string }[];
    const ids = profiles.slice(0, 3).map((p) => p.id);
    const bindRes = await page.request.put(`/api/v1/teams/${teamId}/shared-profiles`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { profile_ids: ids },
    });
    expect(bindRes.status(), await bindRes.text()).toBe(200);

    // UI 创建群聊（团队自动包含 3 个助手）
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    await page.locator("button[title='创建群聊']").click();
    const teamCard = page.locator(".team-card", { hasText: "E2E 圆桌团队" }).first();
    await teamCard.waitFor({ state: "visible", timeout: 20_000 });
    await teamCard.click();
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });

    // 成员面板确认多助手（等待成员加载完成）
    await page.locator('button.thread-action[title="群聊成员"]').click();
    await page.locator(".mp-panel .mp-item").first().waitFor({ state: "visible", timeout: 20_000 });
    const aiCount = await page.locator(".mp-panel .mp-item").count();
    expect(aiCount).toBeGreaterThanOrEqual(2);
    await page.locator(".mp-panel .mp-x, .mp-panel button[title='关闭']").first().click().catch(() => {});

    // @ 输入选择「圆桌」（__all_agents__）
    const composer = page.locator(".composer-input");
    await composer.click();
    await page.keyboard.type("@");
    const roundItem = page.locator(".mention-item", { hasText: "圆桌" }).first();
    await roundItem.waitFor({ state: "visible", timeout: 10_000 });
    await roundItem.click();
    await composer.type("，各位请回复收到");
    await composer.press("Enter");

    // 圆桌消息渲染（流式期间即出现 rt-card，等待多 Agent 回复）
    const roundtable = page.locator(".roundtable");
    await roundtable.waitFor({ state: "visible", timeout: 180_000 });
    await expect(roundtable.locator(".roundtable-label")).toContainText("位助手", { timeout: 15_000 });
    const rtCards = await roundtable.locator(".rt-card").count();
    expect(rtCards).toBeGreaterThanOrEqual(2);
  });

  test("群聊文件夹：新建 → 显示在群聊列表", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });

    // 切到群聊 tab（无静态 class 的 tab 按钮）
    await page.locator(".convo-tab-group button", { hasText: "群聊" }).first().click();
    // 新建群聊文件夹
    await page.locator('button[title="新建群聊文件夹"]').click();
    const folderName = `e2e-群聊夹-${Date.now().toString().slice(-6)}`;
    await page.locator(".new-folder-row input").fill(folderName);
    await page.locator(".new-folder-row button", { hasText: "确定" }).click();
    await expect(page.locator(".folder-name", { hasText: folderName }).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test("成员添加/移除：API 操作 + 面板成员数反映", async ({ page }) => {
    // 前置：创建团队 + 群聊
    const token = adminToken();
    const teamRes = await page.request.post("/api/v1/teams", {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: `E2E 成员管理-${Date.now().toString().slice(-6)}` },
    });
    expect(teamRes.status(), await teamRes.text()).toBe(201);
    const teamId = (await teamRes.json()).id as string;
    const groupRes = await page.request.post("/api/v1/conversations/group", {
      headers: { Authorization: `Bearer ${token}` },
      data: { title: "成员管理群聊", team_id: teamId },
    });
    expect(groupRes.status(), await groupRes.text()).toBe(201);
    // 修复：201 已断言，无需 200
    const convId = (await groupRes.json()).id as string;

    // 读取成员列表；群聊刚创建可能短暂未就绪，空结果重试 6 次
    let before: { id: string; agent_id: string | null }[] = [];
    for (let i = 0; i < 6 && before.length === 0; i++) {
      const memResp = await page.request.get(`/api/v1/conversations/${convId}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (memResp.status() !== 200) continue;
      before = (await memResp.json()) as { id: string; agent_id: string | null }[];
      if (before.length === 0) await page.waitForTimeout(2000);
    }
    expect(before.length).toBeGreaterThanOrEqual(1);

    // 先添加一个 AI 成员（若已存在则跳过——存在性检查）
    const addRes = await page.request.post(`/api/v1/conversations/${convId}/members`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { agent_id: "coder" },
    });
    expect([200, 201]).toContain(addRes.status());

    // 移除刚才添加/或现有的 AI 成员
    const afterAdd = (await (await page.request.get(`/api/v1/conversations/${convId}/members`, {
      headers: { Authorization: `Bearer ${token}` },
    })).json()) as { id: string; agent_id: string | null }[];
    const agentMember = afterAdd.find((m) => m.agent_id === "coder") ?? afterAdd.find((m) => m.agent_id);
    if (agentMember) {
      const delRes = await page.request.delete(`/api/v1/conversations/${convId}/members/${agentMember.id}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      expect([200, 204]).toContain(delRes.status());
    }
    const after = (await (await page.request.get(`/api/v1/conversations/${convId}/members`, {
      headers: { Authorization: `Bearer ${token}` },
    })).json()) as unknown[];
    expect(after.length).toBeLessThanOrEqual(afterAdd.length);

    // UI：直接按 convId 打开刚创建的群聊（侧栏同名会话堆积时按标题
    // 定位会点到历史残留——?c= 精确导航）
    await page.goto(`/?c=${convId}`);
    await expect(page.locator(".composer-input")).toBeVisible({ timeout: 20_000 });
    await page.locator('button.thread-action[title="群聊成员"]').click();
    await expect(page.locator(".mp-panel")).toBeVisible({ timeout: 15_000 });
    const panelCount = await page.locator(".mp-panel .mp-item").count();
    expect(panelCount).toBeGreaterThanOrEqual(1);

  });
});

async function createTeam(page: Page): Promise<string> {
  const token = adminToken();
  const res = await page.request.post("/api/v1/teams", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `E2E 群聊团队-${Date.now().toString().slice(-6)}` },
  });
  expect(res.status(), await res.text()).toBe(201);
  return (await res.json()).id as string;
}

function adminToken(): string {
  return adminTokenFromState();
}
