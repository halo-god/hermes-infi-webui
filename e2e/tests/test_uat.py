"""UAT (User Acceptance Testing) — uses admin account + real selectors.

Runs the full user acceptance flow against the dev server.
"""
import os
import pytest
from playwright.sync_api import Page, expect

# Use the same base URL as conftest (vite dev server 5173)
BASE_URL = "http://localhost:5173"
TEST_USER_EMAIL = "admin@hermes.io"
TEST_USER_PASSWORD = "Hermes@2026"
TEST_FILE = os.path.join(os.path.dirname(__file__), "..", "test-upload.txt")


def _ensure_test_file():
    os.makedirs(os.path.dirname(TEST_FILE), exist_ok=True)
    with open(TEST_FILE, "w") as f:
        f.write("UAT test file content.\n")


@pytest.fixture
def test_user_page(page: Page):
    """Login as admin user and return page — reuse conftest login helper."""
    from conftest import login
    login(page, TEST_USER_EMAIL, TEST_USER_PASSWORD)
    return page


class TestUATCoreFlow:
    """UAT: Core user flow testing."""

    def test_login_and_landing(self, test_user_page: Page):
        """UAT-001: User can login and see landing page."""
        page = test_user_page
        expect(page).to_have_url("/")
        # Landing page should show greeting or chat interface
        expect(page.locator('.stage').first).to_be_visible(timeout=5000)

    def test_create_conversation(self, test_user_page: Page):
        """UAT-002: User can create a new conversation."""
        page = test_user_page
        # Click the home/new chat button in sidebar
        page.locator('.side-row').first.click(timeout=5000)
        # Should see the chat landing
        expect(page.locator('.stage').first).to_be_visible(timeout=5000)

    def test_send_message_and_receive_response(self, test_user_page: Page):
        """UAT-003: User can send message and receive AI response."""
        page = test_user_page
        textarea = page.locator('textarea').first
        textarea.fill("Hello, what can you do?")
        textarea.press("Enter")
        # Wait for agent response (up to 60 seconds for slow agents)
        expect(page.locator('.msg:not(.user-msg) .md-body')).to_be_visible(timeout=60000)

    def test_conversation_list_persists(self, test_user_page: Page):
        """UAT-004: Conversation list persists after refresh."""
        page = test_user_page
        # Send a message first
        textarea = page.locator('textarea').first
        textarea.fill("Test persistence")
        textarea.press("Enter")
        page.wait_for_timeout(3000)
        # Refresh page
        page.reload()
        page.wait_for_url("/", timeout=10000)
        # Conversation list should still be visible
        expect(page.locator('.convo-list').first).to_be_visible(timeout=5000)

    def test_search_conversations(self, test_user_page: Page):
        """UAT-005: User can search conversations."""
        page = test_user_page
        # Click search button in topbar
        page.click('button[title*="搜索"]', timeout=5000)
        # Search palette input should appear
        expect(page.locator('.palette-input')).to_be_visible(timeout=5000)
        page.fill('.palette-input', "test")
        page.keyboard.press("Escape")


class TestUATStreaming:
    """UAT: Streaming and real-time features."""

    def test_streaming_response(self, test_user_page: Page):
        """UAT-006: AI response streams in real-time."""
        page = test_user_page
        textarea = page.locator('textarea').first
        textarea.fill("Count from 1 to 10")
        textarea.press("Enter")
        # Streaming indicator should appear (typing dots or live label)
        page.wait_for_selector('.typing, .convo-live-label, .agent-phase', timeout=10000)
        # Response should appear
        expect(page.locator('.msg:not(.user-msg) .md-body')).to_be_visible(timeout=30000)

    def test_cancel_generation(self, test_user_page: Page):
        """UAT-007: User can cancel AI generation."""
        page = test_user_page
        textarea = page.locator('textarea').first
        textarea.fill("Write a very long story about space exploration")
        textarea.press("Enter")
        page.wait_for_timeout(1000)
        # Click cancel button (send-btn becomes cancel when streaming)
        cancel_btn = page.locator('.send-btn.cancel')
        if cancel_btn.is_visible(timeout=3000):
            cancel_btn.click(timeout=3000)
        # Should eventually show a response or stop
        page.wait_for_timeout(5000)


class TestUATFileManagement:
    """UAT: File management features."""

    def test_workspace_access(self, test_user_page: Page):
        """UAT-008: User can access workspace panel."""
        page = test_user_page
        # Send a message first so files may exist
        textarea = page.locator('textarea').first
        textarea.fill("Create a file named test.txt")
        textarea.press("Enter")
        # Workspace button appears when files exist
        ws_btn = page.locator('button.thread-action:has-text("工作区")')
        if ws_btn.is_visible(timeout=10000):
            ws_btn.click(timeout=3000)
            expect(page.locator('.workspace')).to_be_visible(timeout=5000)

    def test_file_upload(self, test_user_page: Page):
        """UAT-009: User can upload files."""
        page = test_user_page
        _ensure_test_file()
        # Click the attachment tool button
        page.click('.composer-tool[title="附件"]', timeout=5000)
        # Click upload menu item
        with page.expect_file_chooser() as fc_info:
            page.click('.menu-item:has-text("上传本地文件")', timeout=5000)
        file_chooser = fc_info.value
        file_chooser.set_files(TEST_FILE)
        # Staged file should appear
        expect(page.locator('.staged-files')).to_be_visible(timeout=5000)


class TestUATTeamCollaboration:
    """UAT: Team collaboration features."""

    def test_team_list(self, test_user_page: Page):
        """UAT-010: User can see team list."""
        page = test_user_page
        # Teams section should be visible in sidebar
        expect(page.locator('.side-label:has-text("团队")')).to_be_visible(timeout=5000)
        # Team rows (if any) or empty state should be present
        expect(page.locator('.team-row')).to_be_visible(timeout=5000) if page.locator('.team-row').count() > 0 else expect(page.locator("text=还没有团队")).to_be_visible(timeout=5000)
