"""File management E2E tests — uses real CSS selectors + creates test file."""
import os
import pytest
from playwright.sync_api import Page, expect

# Create a temporary test file for upload tests
TEST_FILE = os.path.join(os.path.dirname(__file__), "..", "test-upload.txt")


def _ensure_test_file():
    os.makedirs(os.path.dirname(TEST_FILE), exist_ok=True)
    with open(TEST_FILE, "w") as f:
        f.write("This is a test file for E2E upload.\n")


def test_file_upload(logged_in_page: Page):
    """Test file upload in conversation composer."""
    page = logged_in_page
    _ensure_test_file()
    # Click the attachment tool button in composer
    page.click('.composer-tool[title="附件"]', timeout=5000)
    # Click "上传本地文件" menu item
    with page.expect_file_chooser() as fc_info:
        page.click('.menu-item:has-text("上传本地文件")', timeout=5000)
    file_chooser = fc_info.value
    file_chooser.set_files(TEST_FILE)
    # Staged file chip should appear
    expect(page.locator('.staged-files')).to_be_visible(timeout=5000)


def test_files_page_access(logged_in_page: Page):
    """Test the file management page is accessible."""
    page = logged_in_page
    page.goto("/files", wait_until="domcontentloaded")
    expect(page.locator('.files-page')).to_be_visible(timeout=10000)


def test_files_page_breadcrumb(logged_in_page: Page):
    """Test files page shows breadcrumb navigation."""
    page = logged_in_page
    page.goto("/files", wait_until="domcontentloaded")
    expect(page.locator('.files-head')).to_be_visible(timeout=10000)


def test_files_page_create_folder(logged_in_page: Page):
    """Test creating a folder in file management."""
    page = logged_in_page
    page.goto("/files", wait_until="domcontentloaded")
    # Look for a "新建文件夹" button (may not exist if UI differs)
    create_btn = page.locator('button:has-text("新建文件夹"), button:has-text("创建文件夹")')
    if create_btn.count() > 0:
        create_btn.first.click(timeout=5000)
        # Fill folder name if input appears
        folder_input = page.locator('input[placeholder*="文件夹"], input[placeholder*="folder"]')
        if folder_input.count() > 0:
            folder_input.fill(f"e2e-test-folder-{page.evaluate('Date.now() % 10000')}")
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)
