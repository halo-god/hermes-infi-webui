"""Streaming E2E tests."""
import pytest
from playwright.sync_api import Page, expect


def test_streaming_response(logged_in_page: Page):
    """Test AI response streams in real-time."""
    page = logged_in_page
    # Create new conversation
    page.click('[data-testid="new-chat"]')
    # Send a message
    page.fill('[data-testid="message-input"]', "What is 2+2?")
    page.click('[data-testid="send-button"]')
    # Should show streaming indicator
    expect(page.locator('[data-testid="streaming-indicator"]')).to_be_visible()
    # Wait for response to complete
    expect(page.locator('[data-testid="streaming-indicator"]')).not_to_be_visible(timeout=30000)


def test_cancel_generation(logged_in_page: Page):
    """Test canceling AI generation."""
    page = logged_in_page
    # Create new conversation
    page.click('[data-testid="new-chat"]')
    # Send a message that takes time
    page.fill('[data-testid="message-input"]', "Write a long essay about AI")
    page.click('[data-testid="send-button"]')
    # Click cancel button
    page.click('[data-testid="cancel-button"]')
    # Should stop streaming
    expect(page.locator('[data-testid="streaming-indicator"]')).not_to_be_visible()


def test_message_status(logged_in_page: Page):
    """Test message status indicators."""
    page = logged_in_page
    # Create new conversation
    page.click('[data-testid="new-chat"]')
    # Send a message
    page.fill('[data-testid="message-input"]', "Hello")
    page.click('[data-testid="send-button"]')
    # User message should show as complete
    expect(page.locator('[data-testid="message-status"]')).to_be_visible()


def test_tool_call_display(logged_in_page: Page):
    """Test tool call steps are displayed."""
    page = logged_in_page
    # Create new conversation
    page.click('[data-testid="new-chat"]')
    # Send a message that triggers tool use
    page.fill('[data-testid="message-input"]', "Read the file README.md")
    page.click('[data-testid="send-button"]')
    # Tool call step should appear
    expect(page.locator('[data-testid="tool-call"]')).to_be_visible(timeout=15000)
