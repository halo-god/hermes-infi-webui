import { test, expect, type Page } from "@playwright/test";
import { adminTokenFromState } from "./helpers";

test.describe("项目", () => {
  test("团队 → 项目列表 → 进入项目页（看板/文档）", async ({ page }) => {
    // 前置：通过 API 创建测试团队 + 项目（admin 是创建者，自动为成员）
    const token = adminToken();
    const teamRes = await page.request.post("/api/v1/teams", {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: `E2E 项目团队-${Date.now().toString().slice(-6)}` },
    });
    expect(teamRes.status(), await teamRes.text()).toBe(201);
    const teamId = (await teamRes.json()).id as string;
    const projRes = await page.request.post(`/api/v1/teams/${teamId}/projects`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: "E2E 看板项目", summary: "Playwright 自动化测试项目" },
    });
    expect(projRes.status(), await projRes.text()).toBe(201);

    // UI：进入团队 → 项目 tab → 项目卡片
    await page.goto("/");
    await expect(page.locator(".side")).toBeVisible({ timeout: 20_000 });
    const teamRow = page.locator(".team-row", { hasText: "E2E 项目团队" }).first();
    await teamRow.waitFor({ state: "visible", timeout: 20_000 });
    await teamRow.click();
    await page.waitForURL(/\/teams\/.+/, { timeout: 20_000 });

    await page.locator(".team-tab", { hasText: "项目" }).first().click();
    const projCard = page.locator(".proj-card").first();
    await projCard.waitFor({ state: "visible", timeout: 20_000 });
    await expect(projCard).toContainText("E2E 看板项目");

    // 进入项目页
    await projCard.click();
    await page.waitForURL(/\/projects\/.+/, { timeout: 20_000 });
    await expect(page.locator(".proj-hero, .proj-hero-name, .kanban-board").first()).toBeVisible({
      timeout: 20_000,
    });
  });
});

function adminToken(): string {
  return adminTokenFromState();
}
