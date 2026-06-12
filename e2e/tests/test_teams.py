"""Team management E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_create_team(logged_in_page: Page):
    """Test creating a new team."""
    page = logged_in_page
    # Navigate to teams
    page.click('[data-testid="teams-link"]')
    # Click create team button
    page.click('[data-testid="create-team-button"]')
    # Fill team name
    page.fill('[data-testid="team-name-input"]', "Test Team")
    page.click('[data-testid="create-button"]')
    # Team should be created
    expect(page.locator("text=Test Team")).to_be_visible()


def test_invite_member(logged_in_page: Page):
    """Test inviting a member to team."""
    page = logged_in_page
    # Navigate to team
    page.click('[data-testid="teams-link"]')
    page.click('[data-testid="team-item"]')
    # Click invite button
    page.click('[data-testid="invite-button"]')
    # Fill email
    page.fill('[data-testid="invite-email-input"]', "newmember@test.com")
    page.click('[data-testid="send-invite-button"]')
    # Invitation should be sent
    expect(page.locator("text=邀请已发送")).to_be_visible()


def test_team_settings(logged_in_page: Page):
    """Test team settings page."""
    page = logged_in_page
    # Navigate to team
    page.click('[data-testid="teams-link"]')
    page.click('[data-testid="team-item"]')
    # Click settings tab
    page.click('[data-testid="settings-tab"]')
    # Settings should be visible
    expect(page.locator('[data-testid="team-settings"]')).to_be_visible()


def test_team_members_list(logged_in_page: Page):
    """Test team members list."""
    page = logged_in_page
    # Navigate to team
    page.click('[data-testid="teams-link"]')
    page.click('[data-testid="team-item"]')
    # Members list should be visible
    expect(page.locator('[data-testid="members-list"]')).to_be_visible()


def test_leave_team(logged_in_page: Page):
    """Test leaving a team."""
    page = logged_in_page
    # Navigate to team
    page.click('[data-testid="teams-link"]')
    page.click('[data-testid="team-item"]')
    # Click leave team button
    page.click('[data-testid="leave-team-button"]')
    # Confirm
    page.click('[data-testid="confirm-leave"]')
    # Should be redirected away
    expect(page.locator("text=Test Team")).not_to_be_visible()
