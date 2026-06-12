"""Admin panel E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_admin_access(logged_in_page: Page):
    """Test admin can access admin panel."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Admin panel should be visible
    expect(page.locator('[data-testid="admin-panel"]')).to_be_visible()


def test_user_management(logged_in_page: Page):
    """Test user management in admin."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Click users tab
    page.click('[data-testid="users-tab"]')
    # User list should be visible
    expect(page.locator('[data-testid="user-list"]')).to_be_visible()


def test_create_user(logged_in_page: Page):
    """Test creating a new user."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Click create user button
    page.click('[data-testid="create-user-button"]')
    # Fill user details
    page.fill('[data-testid="user-email-input"]', "newuser@test.com")
    page.fill('[data-testid="user-name-input"]', "New User")
    page.click('[data-testid="create-button"]')
    # User should be created
    expect(page.locator("text=newuser@test.com")).to_be_visible()


def test_user_role_change(logged_in_page: Page):
    """Test changing user role."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Click users tab
    page.click('[data-testid="users-tab"]')
    # Click on user row
    page.click('[data-testid="user-row"]')
    # Change role
    page.select_option('[data-testid="role-select"]', "admin")
    # Save changes
    page.click('[data-testid="save-button"]')
    # Role should be updated
    expect(page.locator("text=admin")).to_be_visible()


def test_deactivate_user(logged_in_page: Page):
    """Test deactivating a user."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Click users tab
    page.click('[data-testid="users-tab"]')
    # Click on user row
    page.click('[data-testid="user-row"]')
    # Click deactivate button
    page.click('[data-testid="deactivate-button"]')
    # Confirm
    page.click('[data-testid="confirm-deactivate"]')
    # User should be deactivated
    expect(page.locator('[data-testid="user-status"]')).to_contain_text("已停用")


def test_audit_log(logged_in_page: Page):
    """Test viewing audit log."""
    page = logged_in_page
    # Navigate to admin
    page.click('[data-testid="admin-link"]')
    # Click audit tab
    page.click('[data-testid="audit-tab"]')
    # Audit log should be visible
    expect(page.locator('[data-testid="audit-log"]')).to_be_visible()
