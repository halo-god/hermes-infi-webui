"""File management E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_file_upload(logged_in_page: Page):
    """Test file upload in conversation."""
    page = logged_in_page
    # Create new conversation
    page.click('[data-testid="new-chat"]')
    # Click upload button
    page.click('[data-testid="upload-button"]')
    # Select a file (using file chooser)
    with page.expect_file_chooser() as fc_info:
        page.click('[data-testid="file-input"]')
    file_chooser = fc_info.value
    file_chooser.set_files("test.txt")
    # File should be staged
    expect(page.locator('[data-testid="staged-file"]')).to_be_visible()


def test_workspace_panel(logged_in_page: Page):
    """Test workspace panel opens and shows files."""
    page = logged_in_page
    # Click workspace button
    page.click('[data-testid="workspace-button"]')
    # Panel should open
    expect(page.locator('[data-testid="workspace-panel"]')).to_be_visible()


def test_file_preview(logged_in_page: Page):
    """Test file preview in workspace."""
    page = logged_in_page
    # Open workspace
    page.click('[data-testid="workspace-button"]')
    # Click on a file to preview
    page.click('[data-testid="file-item"]')
    # Preview should show
    expect(page.locator('[data-testid="file-preview"]')).to_be_visible()


def test_file_download(logged_in_page: Page):
    """Test file download from workspace."""
    page = logged_in_page
    # Open workspace
    page.click('[data-testid="workspace-button"]')
    # Click download button
    page.click('[data-testid="download-button"]')
    # Download should start
    expect(page.locator('[data-testid="download-progress"]')).to_be_visible()
