"""Conversation E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_create_new_conversation(logged_in_page: Page):
    """Test creating a new conversation via the new chat button."""
    page = logged_in_page
    # Click the "+" new chat button in sidebar
    page.click('button:has-text("+")', timeout=5000)
    # Should see the chat interface with assistant selection
    expect(page.locator("text=Ask me anything")).to_be_visible(timeout=5000)


def test_send_message(logged_in_page: Page):
    """Test sending a message in conversation."""
    page = logged_in_page
    # Find the textarea and type message
    textarea = page.locator('textarea')
    textarea.fill("Hello, this is a test message")
    textarea.press("Enter")
    # User message should appear (with longer timeout for slow responses)
    expect(page.locator("text=Hello, this is a test message")).to_be_visible(timeout=15000)


def test_conversation_list(logged_in_page: Page):
    """Test conversation list is displayed in sidebar."""
    page = logged_in_page
    # Sidebar should show conversation section
    expect(page.locator("text=Conversations")).to_be_visible()


def test_search_conversations(logged_in_page: Page):
    """Test searching conversations."""
    page = logged_in_page
    # Use keyboard shortcut to open search
    page.keyboard.press("Control+k")
    # Search input should appear (not dialog)
    page.wait_for_selector('input[placeholder*="搜索"]', timeout=3000)
    # Type search query
    page.fill('input[placeholder*="搜索"]', "test")
    page.keyboard.press("Escape")


def test_conversation_sidebar_toggle(logged_in_page: Page):
    """Test toggling sidebar visibility."""
    page = logged_in_page
    # The toggle button might have a tooltip or aria-label
    page.locator('button:has-text("折叠"), button[title*="折叠"], button[title*="sidebar"]').first.click(timeout=3000)
    page.wait_for_timeout(500)
    # Click again to expand
    page.locator('button:has-text("折叠"), button[title*="折叠"], button[title*="sidebar"]').first.click(timeout=3000)
