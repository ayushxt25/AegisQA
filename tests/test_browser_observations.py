"""Focused tests for bounded, policy-derived browser observations."""

import unittest

from agent_runtime.actions import ActionPolicy
from agent_runtime.browser import PlaywrightBrowserTool
from agent_runtime.state import Observation
from agent_experiment import fixture_assertion


class FakeLocator:
    def __init__(self, metadata=None, text=None, visible=True, enabled=True, count=1):
        self.metadata = metadata or {}
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.element_count = count

    def count(self):
        return self.element_count

    def evaluate(self, script, timeout):
        return self.metadata

    def is_visible(self, timeout):
        return self.visible

    def is_enabled(self, timeout):
        return self.enabled

    def text_content(self, timeout):
        return self.text


class FakePage:
    def __init__(self, locators, url="file:///fixture/index.html?token=not-observed", title="Fixture"):
        self.locators = locators
        self.url = url
        self._title = title

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(count=0))

    def title(self):
        return self._title


def policy(click=None, fill=None):
    return ActionPolicy(
        allowed_urls={"file:///fixture/index.html"},
        allowed_click_targets=set(click or []),
        allowed_fill_targets=set(fill or []),
        allowed_inspect_targets={"#result"},
    )


def metadata(tag="button", role="button", name="Increment", sensitive=False):
    return {"tag": tag, "role": role, "accessibleName": name, "sensitive": sensitive}


class BrowserObservationTest(unittest.TestCase):
    def test_structured_observation_is_policy_derived(self):
        page = FakePage(
            {
                "#result": FakeLocator(text="Clicks: 0"),
                "#increment": FakeLocator(metadata=metadata()),
                "#unapproved": FakeLocator(metadata=metadata(name="Unapproved")),
            }
        )
        observation = PlaywrightBrowserTool(page, policy(click={"#increment"})).observe()

        self.assertEqual(observation.data["url"], "file:///fixture/index.html")
        self.assertEqual(observation.data["title"], "Fixture")
        self.assertEqual(observation.data["result"], "Clicks: 0")
        self.assertEqual(
            observation.data["permitted_elements"],
            [{"selector": "#increment", "permitted_actions": ["click"], "available": True,
              "tag": "button", "role": "button", "accessible_name": "Increment",
              "visible": True, "enabled": True}],
        )

    def test_visibility_and_enabled_state_are_reported(self):
        page = FakePage({"#result": FakeLocator(text="Clicks: 0"),
                         "#increment": FakeLocator(metadata=metadata(), visible=False, enabled=False)})
        element = PlaywrightBrowserTool(page, policy(click={"#increment"})).observe().data["permitted_elements"][0]
        self.assertFalse(element["visible"])
        self.assertFalse(element["enabled"])

    def test_missing_permitted_element_does_not_crash_observation(self):
        page = FakePage({"#result": FakeLocator(text="Clicks: 0")})
        element = PlaywrightBrowserTool(page, policy(click={"#missing"})).observe().data["permitted_elements"][0]
        self.assertEqual(element, {"selector": "#missing", "permitted_actions": ["click"], "available": False})

    def test_observation_limit_is_deterministic(self):
        page = FakePage({"#result": FakeLocator(text="Clicks: 0"), "#a": FakeLocator(metadata=metadata(name="A")),
                         "#b": FakeLocator(metadata=metadata(name="B")), "#c": FakeLocator(metadata=metadata(name="C"))})
        observation = PlaywrightBrowserTool(page, policy(click={"#c", "#a", "#b"}), max_observed_elements=2).observe()
        self.assertEqual([item["selector"] for item in observation.data["permitted_elements"]], ["#a", "#b"])
        self.assertTrue(observation.data["permitted_elements_truncated"])

    def test_sensitive_values_and_fields_are_excluded(self):
        page = FakePage({"#result": FakeLocator(text="Clicks: 0"),
                         "#password": FakeLocator(metadata=metadata(tag="input", role="textbox", name="secret-value", sensitive=True))})
        observation = PlaywrightBrowserTool(page, policy(fill={"#password"})).observe()
        self.assertEqual(observation.data["permitted_elements"], [])
        self.assertNotIn("secret-value", str(observation.data))

    def test_existing_deterministic_assertion_is_unchanged(self):
        self.assertTrue(fixture_assertion(Observation("browser", {"result": "Clicks: 1"})))
        self.assertFalse(fixture_assertion(Observation("browser", {"result": "Clicks: 0"})))
