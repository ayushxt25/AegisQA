"""Playwright checks for the controlled P0.4C browser fixtures."""

import unittest
from pathlib import Path

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
else:
    PLAYWRIGHT_AVAILABLE = True


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = PROJECT_ROOT / "fixture" / "p0.4c"
DEFAULT_TIMEOUT_MS = 1_000


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright is not installed")
class ControlledFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, timeout=DEFAULT_TIMEOUT_MS)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def fixture_url(self, filename):
        return (FIXTURE_DIRECTORY / filename).resolve().as_uri()

    def test_f02_disabled_button_is_visible_and_cannot_be_clicked(self):
        self.page.goto(self.fixture_url("disabled-button.html"), wait_until="load")
        button = self.page.get_by_role("button", name="Increment")
        self.assertTrue(button.is_visible())
        self.assertFalse(button.is_enabled())
        with self.assertRaises(PlaywrightTimeoutError):
            button.click(timeout=250)
        self.page.evaluate("document.getElementById('increment').click()")
        self.assertEqual(self.page.evaluate("window.aegisqaDisabledClickCount"), 0)
        self.assertEqual(self.page.locator("#result").text_content(), "Clicks: 0")

    def test_f03_click_executes_but_independent_result_assertion_fails(self):
        self.page.goto(self.fixture_url("incorrect-result.html"), wait_until="load")
        self.page.get_by_role("button", name="Increment").click()
        self.assertEqual(self.page.evaluate("window.aegisqaActionCount"), 1)
        actual_result = self.page.locator("#result").text_content()
        self.assertEqual(actual_result, "Clicks: 0")
        self.assertNotEqual(actual_result, "Clicks: 1")

    def test_f05_control_and_boundary_delays_are_deterministic_and_bounded(self):
        self.page.goto(self.fixture_url("delayed-result.html"), wait_until="load")
        self.page.evaluate("window.aegisqaConfigureRenderingDelay('control', 0)")
        self.page.get_by_role("button", name="Increment").click()
        self.page.wait_for_function("document.getElementById('result').textContent === 'Clicks: 1'", timeout=250)

        boundary_context = self.browser.new_context()
        try:
            boundary_context.set_default_timeout(DEFAULT_TIMEOUT_MS)
            boundary_page = boundary_context.new_page()
            boundary_page.goto(self.fixture_url("delayed-result.html"), wait_until="load")
            boundary_page.evaluate("window.aegisqaConfigureRenderingDelay('boundary', 300)")
            boundary_page.get_by_role("button", name="Increment").click()
            with self.assertRaises(PlaywrightTimeoutError):
                boundary_page.wait_for_function("document.getElementById('result').textContent === 'Clicks: 1'", timeout=100)
            boundary_page.wait_for_function("document.getElementById('result').textContent === 'Clicks: 1'", timeout=500)
            with self.assertRaises(PlaywrightError):
                boundary_page.evaluate("window.aegisqaConfigureRenderingDelay('boundary', 1001)")
        finally:
            boundary_context.close()


if __name__ == "__main__":
    unittest.main()
