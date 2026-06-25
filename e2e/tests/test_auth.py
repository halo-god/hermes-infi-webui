"""Authentication E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_login_success(logged_in_page: Page):
    """Test successful login redirects to home."""
    page = logged_in_page
    expect(page).to_have_url("/")
    # Should see the main chat interface
    expect(page.locator("text=Ask me anything")).to_be_visible()


def test_login_invalid_credentials(page: Page):
    """Test login with invalid credentials shows error."""
    page.goto("/login")
    page.fill('input[type="text"]', "wrong@email.com")
    page.fill('input[type="password"]', "wrongpassword")
    page.click('button[type="submit"]')
    # Should stay on login page
    expect(page).to_have_url("/login")


def test_logout(logged_in_page: Page):
    """Test logout redirects to login page."""
    page = logged_in_page
    # Click the logout button in sidebar (class side-logout, title is i18n "退出登录")
    page.click('button.side-logout', timeout=5000)
    expect(page).to_have_url("/login")


def test_protected_route_redirect(page: Page):
    """Test protected route redirects to login."""
    page.goto("/admin")
    # Should redirect to login
    page.wait_for_load_state("networkidle")
    assert "/login" in page.url


def test_session_persistence(logged_in_page: Page):
    """Test session persists after page reload."""
    page = logged_in_page
    page.reload()
    expect(page).to_have_url("/")
    expect(page.locator("text=Ask me anything")).to_be_visible()
