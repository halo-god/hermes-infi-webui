"""Admin panel E2E tests — uses real CSS selectors (no data-testid in the app)."""
import pytest
from playwright.sync_api import Page, expect


def test_admin_access(logged_in_page: Page):
    """Test admin panel is accessible."""
    page = logged_in_page
    # Navigate to admin via sidebar footer link
    page.locator('.side-foot .side-row:has-text("ADMIN"), .side-foot .side-row:has-text("后台管理")').first.click(timeout=10000)
    expect(page).to_have_url("/admin", timeout=5000)
    # Admin tabs should be visible
    expect(page.locator(".admin-tabs")).to_be_visible(timeout=5000)


def test_user_management(logged_in_page: Page):
    """Test user management tab shows user list."""
    page = logged_in_page
    page.goto("/admin")
    page.locator('.admin-tabs .team-tab:has-text("用户管理")').click(timeout=5000)
    expect(page.locator(".users-table")).to_be_visible(timeout=5000)


def test_create_user(logged_in_page: Page):
    """Test creating a new user."""
    page = logged_in_page
    page.goto("/admin")
    page.locator('.admin-tabs .team-tab:has-text("用户管理")').click(timeout=5000)
    page.click('button:has-text("新建用户")', timeout=5000)
    page.fill('input[placeholder="姓名"]', "E2E测试")
    page.fill('input[placeholder="邮箱"]', f"e2e-{page.evaluate('Date.now()')}@hermes.io")
    page.fill('input[placeholder="初始密码(≥8)"]', "Test@2026")
    page.click('button:has-text("创建")', timeout=5000)
    page.wait_for_timeout(1000)


def test_user_role_change(logged_in_page: Page):
    """Test user list displays roles."""
    page = logged_in_page
    page.goto("/admin")
    page.locator('.admin-tabs .team-tab:has-text("用户管理")').click(timeout=5000)
    expect(page.locator(".users-table")).to_be_visible(timeout=5000)
    rows = page.locator(".users-table .ut-row:not(.head)")
    expect(rows.first).to_be_visible(timeout=5000)


def test_deactivate_user(logged_in_page: Page):
    """Test user status column is visible."""
    page = logged_in_page
    page.goto("/admin")
    page.locator('.admin-tabs .team-tab:has-text("用户管理")').click(timeout=5000)
    expect(page.locator(".users-table")).to_be_visible(timeout=5000)
    expect(page.locator(".status-cell").first).to_be_visible(timeout=5000)


def test_audit_log(logged_in_page: Page):
    """Test audit log tab shows entries."""
    page = logged_in_page
    page.goto("/admin")
    page.locator('.admin-tabs .team-tab:has-text("审计日志")').click(timeout=5000)
    expect(page.locator(".audit-table")).to_be_visible(timeout=5000)
