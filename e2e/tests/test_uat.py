"""UAT (User Acceptance Testing) conftest."""
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8080"
TEST_USER_EMAIL = "006487@wecom.infiled.com"
TEST_USER_PASSWORD = "test123"  # Placeholder - use actual password


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "base_url": BASE_URL}


@pytest.fixture
def test_user_page(page: Page):
    """Login as test user and return page."""
    page.goto("/login")
    page.fill('input[type="text"]', TEST_USER_EMAIL)
    page.fill('input[type="password"]', TEST_USER_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url("/")
    return page


class TestUATCoreFlow:
    """UAT: Core user flow testing."""

    def test_login_and_landing(self, test_user_page: Page):
        """UAT-001: User can login and see landing page."""
        page = test_user_page
        expect(page).to_have_url("/")
        expect(page.locator("text=新会话")).to_be_visible()

    def test_create_conversation(self, test_user_page: Page):
        """UAT-002: User can create a new conversation."""
        page = test_user_page
        page.click('[data-testid="new-chat"]')
        expect(page.locator("text=新会话")).to_be_visible()

    def test_send_message_and_receive_response(self, test_user_page: Page):
        """UAT-003: User can send message and receive AI response."""
        page = test_user_page
        page.click('[data-testid="new-chat"]')
        page.fill('[data-testid="message-input"]', "Hello, what can you do?")
        page.click('[data-testid="send-button"]')
        # Wait for response (up to 30 seconds)
        expect(page.locator('[data-testid="agent-message"]')).to_be_visible(timeout=30000)

    def test_conversation_list_persists(self, test_user_page: Page):
        """UAT-004: Conversation list persists after refresh."""
        page = test_user_page
        # Create conversation
        page.click('[data-testid="new-chat"]')
        page.fill('[data-testid="message-input"]', "Test message")
        page.click('[data-testid="send-button"]')
        # Wait for response
        page.wait_for_timeout(2000)
        # Refresh page
        page.reload()
        # Conversation should still be in list
        expect(page.locator('[data-testid="conversation-list"]')).to_be_visible()

    def test_search_conversations(self, test_user_page: Page):
        """UAT-005: User can search conversations."""
        page = test_user_page
        # Type in search
        page.fill('[data-testid="search-input"]', "test")
        # Should filter results
        expect(page.locator('[data-testid="conversation-list"]')).to_be_visible()


class TestUATStreaming:
    """UAT: Streaming and real-time features."""

    def test_streaming_response(self, test_user_page: Page):
        """UAT-006: AI response streams in real-time."""
        page = test_user_page
        page.click('[data-testid="new-chat"]')
        page.fill('[data-testid="message-input"]', "Count from 1 to 10")
        page.click('[data-testid="send-button"]')
        # Streaming indicator should appear
        expect(page.locator('[data-testid="streaming-indicator"]')).to_be_visible()
        # Response should stream in
        expect(page.locator('[data-testid="agent-message"]')).to_be_visible(timeout=30000)

    def test_cancel_generation(self, test_user_page: Page):
        """UAT-007: User can cancel AI generation."""
        page = test_user_page
        page.click('[data-testid="new-chat"]')
        page.fill('[data-testid="message-input"]', "Write a very long story")
        page.click('[data-testid="send-button"]')
        page.wait_for_timeout(1000)
        # Cancel
        page.click('[data-testid="cancel-button"]')
        # Should stop
        expect(page.locator('[data-testid="streaming-indicator"]')).not_to_be_visible()


class TestUATFileManagement:
    """UAT: File management features."""

    def test_workspace_access(self, test_user_page: Page):
        """UAT-008: User can access workspace."""
        page = test_user_page
        page.click('[data-testid="workspace-button"]')
        expect(page.locator('[data-testid="workspace-panel"]')).to_be_visible()

    def test_file_upload(self, test_user_page: Page):
        """UAT-009: User can upload files."""
        page = test_user_page
        page.click('[data-testid="new-chat"]')
        # Upload file
        with page.expect_file_chooser() as fc_info:
            page.click('[data-testid="upload-button"]')
        file_chooser = fc_info.value
        file_chooser.set_files("test.txt")
        expect(page.locator('[data-testid="staged-file"]')).to_be_visible()


class TestUATTeamCollaboration:
    """UAT: Team collaboration features."""

    def test_team_list(self, test_user_page: Page):
        """UAT-010: User can view team list."""
        page = test_user_page
        page.click('[data-testid="teams-link"]')
        expect(page.locator('[data-testid="teams-list"]')).to_be_visible()
