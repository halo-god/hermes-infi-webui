"""Team management E2E tests — uses real CSS selectors."""
import pytest
from playwright.sync_api import Page, expect


def test_create_team(logged_in_page: Page):
    """Test creating a new team via NewTeamModal."""
    page = logged_in_page
    # Click the "+" button next to "团队" label in sidebar
    page.locator('.side-label:has-text("团队") button').click(timeout=5000)
    # Fill team name in the modal
    page.fill('.np-name', f"E2E Team {page.evaluate('Date.now() % 100000')}", timeout=5000)
    # Click create button
    page.click('button:has-text("创建团队")', timeout=5000)
    # Should navigate to the team page
    page.wait_for_timeout(2000)
    import re
    expect(page).to_have_url(re.compile(r"/teams/"), timeout=10000)


def test_team_detail_page(logged_in_page: Page):
    """Test team detail page is accessible."""
    page = logged_in_page
    # If there are teams, click the first one
    team_rows = page.locator('.team-row')
    if team_rows.count() > 0:
        team_rows.first.click(timeout=5000)
        page.wait_for_load_state("networkidle")
        # Team detail page should show team content
        expect(page.locator('.stage, .team-detail')).to_be_visible(timeout=5000)
    else:
        # No teams exist — skip
        pytest.skip("No teams available to test")


def test_team_members_display(logged_in_page: Page):
    """Test team members are displayed on team page."""
    page = logged_in_page
    team_rows = page.locator('.team-row')
    if team_rows.count() == 0:
        pytest.skip("No teams available to test")
    team_rows.first.click(timeout=5000)
    page.wait_for_load_state("networkidle")
    # Member avatars or member list should be visible
    expect(page.locator('.mem-avatar, .member-row, .team-members').first).to_be_visible(timeout=5000)


def test_team_activity(logged_in_page: Page):
    """Test team page shows activity or content."""
    page = logged_in_page
    team_rows = page.locator('.team-row')
    if team_rows.count() == 0:
        pytest.skip("No teams available to test")
    team_rows.first.click(timeout=5000)
    page.wait_for_load_state("networkidle")
    # The team detail page should have some content area
    expect(page.locator('.stage')).to_be_visible(timeout=5000)


def test_team_navigation_back(logged_in_page: Page):
    """Test navigating to a team and back."""
    page = logged_in_page
    team_rows = page.locator('.team-row')
    if team_rows.count() == 0:
        pytest.skip("No teams available to test")
    team_rows.first.click(timeout=5000)
    page.wait_for_load_state("networkidle")
    # Navigate back to home
    page.goto("/")
    expect(page).to_have_url("/", timeout=5000)
