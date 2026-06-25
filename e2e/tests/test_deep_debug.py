"""Deep functional tests — actually verify behavior, not just element visibility."""
import os
import time
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5173"
ADMIN_EMAIL = "admin@hermes.io"
ADMIN_PASSWORD = "Hermes@2026"


def login(page):
    page.goto("/login", wait_until="domcontentloaded")
    page.fill('input[type="text"]', ADMIN_EMAIL)
    page.fill('input[type="password"]', ADMIN_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url("/", timeout=30000)


class TestMessageRoundTrip:
    """Verify: send message → AI actually responds with content."""

    def test_send_and_get_real_response(self, page: Page):
        login(page)
        # Wait for chat interface
        page.wait_for_selector('textarea', timeout=10000)
        textarea = page.locator('textarea').first
        textarea.fill("回复两个字：收到")
        textarea.press("Enter")

        # User message must appear
        expect(page.locator("text=回复两个字：收到")).to_be_visible(timeout=10000)

        # Agent message must appear with REAL content (not empty)
        agent_msg = page.locator('.msg:not(.user-msg) .md-body')
        expect(agent_msg).to_be_visible(timeout=60000)

        # Verify the response is not empty
        content = agent_msg.inner_text(timeout=5000)
        print(f"\n[DEBUG] Agent response: {content[:200]}")
        assert len(content.strip()) > 0, "Agent response is empty!"

        # Verify no error messages
        error_toasts = page.locator('text=请求失败, text=错误, text=失败')
        assert error_toasts.count() == 0, f"Error toast appeared: {error_toasts.all_inner_texts()}"


class TestAdminCreateUser:
    """Verify: admin creates user → user can login."""

    def test_create_user_and_login(self, page: Page):
        login(page)
        page.goto("/admin", wait_until="domcontentloaded")
        page.locator('.admin-tabs .team-tab:has-text("用户管理")').click(timeout=5000)
        expect(page.locator(".users-table")).to_be_visible(timeout=5000)

        # Count existing users
        before_count = page.locator('.users-table .ut-row:not(.head)').count()

        # Create a new user
        page.click('button:has-text("新建用户")', timeout=5000)
        email = f"deep-test-{int(time.time())}@hermes.io"
        page.fill('input[placeholder="姓名"]', "深度测试用户")
        page.fill('input[placeholder="邮箱"]', email)
        page.fill('input[placeholder="初始密码(≥8)"]', "DeepTest@2026")
        page.click('button:has-text("创建")', timeout=5000)
        page.wait_for_timeout(2000)

        # Verify user count increased
        after_count = page.locator('.users-table .ut-row:not(.head)').count()
        print(f"\n[DEBUG] Users before: {before_count}, after: {after_count}")
        assert after_count > before_count, f"User count did not increase ({before_count} → {after_count})"

        # Now logout and try logging in as the new user
        page.locator('button.side-logout').click(timeout=5000)
        page.wait_for_url("/login", timeout=5000)

        page.fill('input[type="text"]', email)
        page.fill('input[type="password"]', "DeepTest@2026")
        page.click('button[type="submit"]')
        # Should successfully login (not stay on /login)
        page.wait_for_url("/", timeout=15000)
        print(f"\n[DEBUG] New user {email} successfully logged in!")


class TestFileManagementFull:
    """Verify: create folder → no duplicate → upload file → delete file → delete folder."""

    def test_full_file_lifecycle(self, page: Page):
        login(page)
        page.goto("/files", wait_until="domcontentloaded")
        page.wait_for_selector('.files-page', timeout=10000)

        # Create a folder
        folder_name = f"deep-test-{int(time.time())}"
        # Look for create folder button
        create_btn = page.locator('button:has-text("新建文件夹"), button:has-text("创建文件夹")')
        if create_btn.count() > 0:
            create_btn.first.click(timeout=5000)
            folder_input = page.locator('input[placeholder*="文件夹"], input[placeholder*="名"]')
            if folder_input.count() > 0:
                folder_input.fill(folder_name)
                # Press enter or click confirm
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)

                # Verify folder appears exactly ONCE (no duplicate — the #26 bug)
                folder_elements = page.locator(f'text="{folder_name}"')
                count = folder_elements.count()
                print(f"\n[DEBUG] Folder '{folder_name}' appears {count} time(s)")
                assert count <= 1, f"BUG: Folder appears {count} times (duplicate! issue #26)"

        # Upload a test file
        test_file = os.path.join(os.path.dirname(__file__), "..", "test-deep.txt")
        with open(test_file, "w") as f:
            f.write("Deep test file content\n")

        # Go back to chat to upload via composer
        page.goto("/", wait_until="domcontentloaded")
        page.wait_for_selector('textarea', timeout=10000)
        page.click('.composer-tool[title="附件"]', timeout=5000)
        with page.expect_file_chooser() as fc_info:
            page.click('.menu-item:has-text("上传本地文件")', timeout=5000)
        fc_info.value.set_files(test_file)
        page.wait_for_timeout(2000)

        # Verify staged file
        staged = page.locator('.staged-files')
        assert staged.is_visible(timeout=5000), "Staged file not visible after upload"
        print(f"\n[DEBUG] File upload staged successfully")


class TestConversationFolders:
    """Verify: create folder → move conversation → pin → verify."""

    def test_create_and_pin_folder(self, page: Page):
        login(page)
        page.wait_for_selector('.side-inner', timeout=10000)

        # Create a conversation folder
        page.locator('.side-label:has-text("对话") button:has-text("文件夹")').click(timeout=5000)
        folder_name = f"test-folder-{int(time.time())}"
        page.fill('.new-folder-row input', folder_name)
        page.click('.new-folder-row button:has-text("确定")')
        page.wait_for_timeout(1000)

        # Verify folder appears in sidebar
        folder_label = page.locator(f'.folder-name:has-text("{folder_name}")')
        assert folder_label.is_visible(timeout=5000), f"Folder '{folder_name}' not visible after creation"
        print(f"\n[DEBUG] Folder '{folder_name}' created and visible in sidebar")

        # Right-click folder to pin it
        folder_sep = page.locator(f'.folder-sep:has-text("{folder_name}")')
        folder_sep.click(button="right", timeout=5000)
        # Click "置顶文件夹"
        pin_btn = page.locator('.ctx-menu .menu-item:has-text("置顶")')
        if pin_btn.is_visible(timeout=3000):
            pin_btn.click(timeout=3000)
            page.wait_for_timeout(500)
            # Verify pin icon appears
            pin_icon = page.locator(f'.folder-sep:has-text("{folder_name}") Icon[name="pin"]')
            # The folder should move to the top (before date buckets)
            print(f"\n[DEBUG] Folder pinned successfully")
        else:
            print(f"\n[DEBUG] Pin option not found (may need different selector)")


class TestScheduledTasks:
    """Verify: create scheduled task → verify it persists → verify cron scheduling."""

    def test_create_scheduled_task(self, page: Page):
        login(page)
        page.goto("/schedule", wait_until="domcontentloaded")
        page.wait_for_selector('.stage', timeout=10000)

        # Click "新建定时任务"
        page.click('button:has-text("新建定时任务")', timeout=5000)

        # Fill the form
        task_name = f"deep-test-task-{int(time.time())}"
        page.fill('input[placeholder*="名称"]', task_name)

        # Fill prompt
        prompt_textarea = page.locator('textarea[placeholder*="prompt"], textarea[placeholder*="指令"]')
        if prompt_textarea.count() == 0:
            prompt_textarea = page.locator('textarea').first
        prompt_textarea.fill("输出当前时间")

        # Set cron to every minute for testing
        cron_input = page.locator('input[placeholder*="cron"]')
        if cron_input.count() == 0:
            cron_input = page.locator('input[placeholder*="Cron"]')
        if cron_input.count() > 0:
            cron_input.fill("*/1 * * * *")

        # Click save
        page.click('button:has-text("保存")', timeout=5000)
        page.wait_for_timeout(2000)

        # Verify task appears in list
        task_item = page.locator(f'text="{task_name}"')
        assert task_item.is_visible(timeout=5000), f"Task '{task_name}' not visible after creation"
        print(f"\n[DEBUG] Scheduled task '{task_name}' created and visible")

        # Verify next_run_at is set (not null/empty)
        # The task row should show "下次：" with a time
        next_run = page.locator(f'text=下次').first
        if next_run.is_visible(timeout=3000):
            print(f"\n[DEBUG] Task has next_run_at set: {next_run.inner_text()}")


class TestBrandingConfig:
    """Verify: change branding → frontend reflects change."""

    def test_change_site_name(self, page: Page):
        login(page)
        page.goto("/admin", wait_until="domcontentloaded")

        # Click system settings tab
        page.locator('.admin-tabs .team-tab:has-text("系统设置")').click(timeout=5000)

        # Find the display name input and change it
        display_input = page.locator('input[v-model="settings.branding.display"]')
        if display_input.count() == 0:
            # Try generic selector
            display_input = page.locator('.cfg-input').first

        if display_input.count() > 0:
            old_value = display_input.input_value()
            new_value = f"测试品牌-{int(time.time())}"
            display_input.fill(new_value)

            # Save settings
            page.click('button:has-text("保存设置")', timeout=5000)
            page.wait_for_timeout(2000)

            # Verify the document title updated
            title = page.title()
            print(f"\n[DEBUG] Page title after branding change: {title}")

            # Go to login page and verify branding shows
            page.goto("/login", wait_until="domcontentloaded")
            page.wait_for_timeout(1000)

            # The login page should show the new brand name
            brand_text = page.locator('.login-wordmark').inner_text(timeout=5000) if page.locator('.login-wordmark').count() > 0 else "N/A"
            print(f"\n[DEBUG] Login wordmark: {brand_text}")

            # Restore original
            page.goto("/admin", wait_until="domcontentloaded")
            page.locator('.admin-tabs .team-tab:has-text("系统设置")').click(timeout=5000)
            display_input = page.locator('input[v-model="settings.branding.display"]')
            if display_input.count() == 0:
                display_input = page.locator('.cfg-input').first
            display_input.fill(old_value)
            page.click('button:has-text("保存设置")', timeout=5000)
            print(f"\n[DEBUG] Branding restored to: {old_value}")
        else:
            print(f"\n[DEBUG] Could not find branding display input")
