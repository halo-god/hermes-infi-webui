import { test, expect } from "@playwright/test";

test.describe("终端", () => {
  test("连接终端并执行命令，输出回显", async ({ page }) => {
    await page.goto("/terminal");
    await expect(page.locator(".terminal-page")).toBeVisible({ timeout: 20_000 });

    // xterm 渲染完成（辅助输入 textarea 出现）
    const xtermInput = page.locator(".xterm-helper-textarea, .xterm textarea").first();
    await xtermInput.waitFor({ state: "visible", timeout: 20_000 });

    // 执行命令并等待输出回显（真实 shell）
    const marker = `e2e-term-${Date.now().toString().slice(-6)}`;
    await xtermInput.click();
    await page.keyboard.type(`echo ${marker}`);
    await page.keyboard.press("Enter");

    await expect
      .poll(
        async () => (await page.locator(".xterm-rows").innerText()).includes(marker),
        { timeout: 30_000, intervals: [1_000] },
      )
      .toBe(true);
  });
});
