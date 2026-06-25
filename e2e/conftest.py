import os
import platform
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5173"
ADMIN_EMAIL = "admin@hermes.io"
ADMIN_PASSWORD = "Hermes@2026"

# Auto-detect the chromium binary path based on the OS.
def _find_chromium() -> str | None:
    if platform.system() == "Darwin":
        # macOS: Playwright installs Chrome for Testing as an .app bundle
        base = os.path.expanduser("~/Library/Caches/ms-playwright")
        if os.path.isdir(base):
            for name in sorted(os.listdir(base), reverse=True):
                if name.startswith("chromium-"):
                    app = os.path.join(
                        base, name, "chrome-mac-arm64",
                        "Google Chrome for Testing.app", "Contents", "MacOS",
                        "Google Chrome for Testing",
                    )
                    if os.path.isfile(app):
                        return app
                    # Intel mac fallback
                    app_intel = os.path.join(
                        base, name, "chrome-mac-x64",
                        "Google Chrome for Testing.app", "Contents", "MacOS",
                        "Google Chrome for Testing",
                    )
                    if os.path.isfile(app_intel):
                        return app_intel
    return None


CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH") or _find_chromium()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "base_url": BASE_URL}


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    args = {**browser_type_launch_args}
    if CHROMIUM_PATH:
        args["executable_path"] = CHROMIUM_PATH
    # --no-sandbox is only needed on Linux CI; on macOS it's unnecessary.
    if platform.system() != "Darwin":
        args["args"] = ["--no-sandbox"]
    return args


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
    # Wait for navigation with longer timeout (agent runner may be busy)
    page.wait_for_url("/", timeout=30000)
