"""Streaming E2E tests — uses real CSS selectors."""
import pytest
from playwright.sync_api import Page, expect


def test_streaming_response(logged_in_page: Page):
    """Test AI response streams in real-time."""
    page = logged_in_page
    # Send a message via the composer textarea
    textarea = page.locator('textarea').first
    textarea.fill("What is 2+2?")
    textarea.press("Enter")
    # Should show streaming indicator (typing dots or live label in sidebar)
    expect(page.locator('.typing, .convo-live-label, .agent-phase').first).to_be_visible(timeout=10000)
    # Wait for response to complete (indicators disappear)
    page.wait_for_selector('.msg:not(.user-msg) .md-body', timeout=30000)


def test_cancel_generation(logged_in_page: Page):
    """Test canceling AI generation."""
    page = logged_in_page
    textarea = page.locator('textarea').first
    textarea.fill("Write a very long essay about the history of computing")
    textarea.press("Enter")
    # Wait for streaming to start
    page.wait_for_selector('.typing, .convo-live-label', timeout=10000)
    # Click the cancel button (send-btn becomes cancel when streaming)
    page.click('.send-btn.cancel', timeout=5000)
    # Streaming should stop
    page.wait_for_selector('.msg:not(.user-msg) .md-body', timeout=15000)


def test_message_status(logged_in_page: Page):
    """Test message appears after sending."""
    page = logged_in_page
    textarea = page.locator('textarea').first
    textarea.fill("Hello")
    textarea.press("Enter")
    # User message should appear
    expect(page.locator("text=Hello")).to_be_visible(timeout=10000)
    # Agent response should appear
    expect(page.locator('.msg:not(.user-msg) .md-body')).to_be_visible(timeout=30000)


def test_tool_call_display(logged_in_page: Page):
    """Test agent execution steps are displayed."""
    page = logged_in_page
    textarea = page.locator('textarea').first
    textarea.fill("Create a file named hello.txt with the content 'Hello World'")
    textarea.press("Enter")
    # Agent steps or files should appear (msg-steps or workspace files)
    page.wait_for_selector('.msg-steps, .ws-file, .md-body', timeout=30000)
