import { test, expect } from "@playwright/test";
import { login, createMemberUser } from "./helpers";

// 认证测试需要真实的未登录/独立登录状态，不复用全局 storageState
test.use({ storageState: { cookies: [], origins: [] } });

test.describe("认证与路由守卫", () => {
  test("未登录访问首页会跳转到登录页", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/login/, { timeout: 15_000 });
    await expect(page.locator(".login-card")).toBeVisible();
  });

  test("管理员登录成功并进入工作区", async ({ page }) => {
    await login(page);
    // 登录后落在聊天首页，侧边栏可见
    await expect(page.locator(".side")).toBeVisible();
    await expect(page.locator(".dock, .composer-input").first()).toBeVisible({ timeout: 20_000 });
  });

  test("非管理员访问 /admin 被重定向回首页", async ({ page }) => {
    const email = `e2e-member-${Date.now()}@hermes.io`;
    await createMemberUser(page, email);
    await login(page, email, "Member@2026");
    await page.goto("/admin");
    await page.waitForURL((url) => !url.pathname.startsWith("/admin"), { timeout: 15_000 });
    await expect(page.locator(".side")).toBeVisible();
  });

  test("错误密码登录失败并显示错误", async ({ page }) => {
    await page.goto("/login");
    await page.locator(".login-input").first().fill(process.env.E2E_ADMIN_EMAIL || "admin@hermes.io");
    await page.locator(".login-input").nth(1).fill("wrong-password-123");
    await page.locator(".login-submit").click();
    await expect(page.locator(".login-error")).toBeVisible({ timeout: 15_000 });
  });
});
