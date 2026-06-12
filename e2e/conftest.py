import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5173"
ADMIN_EMAIL = "admin@hermes.io"
ADMIN_PASSWORD = "Hermes@2026"
CHROMIUM_PATH = "/home/test/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "base_url": BASE_URL}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "executable_path": CHROMIUM_PATH, "args": ["--no-sandbox"]}


@pytest.fixture
def logged_in_page(page: Page):
    """Login as admin and return page."""
    login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    return page


def login(page: Page, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD):
    """Perform login flow."""
    page.goto("/login")
    page.fill('input[type="text"]', email)
    page.fill('input[type="password"]', password)
    page.click('button[type="submit"]')
    # Wait for navigation with longer timeout
    page.wait_for_url("/", timeout=15000)
