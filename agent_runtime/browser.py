"""Thin browser-tool adapter; it accepts already validated actions only."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol
from urllib.parse import urlsplit, urlunsplit

from .actions import ActionKind, ActionPolicy, BrowserAction
from .state import Observation


MAX_OBSERVED_ELEMENTS = 10
MAX_ACCESSIBLE_NAME_CHARS = 80
MAX_TITLE_CHARS = 200
OBSERVATION_TIMEOUT_MS = 250
_METADATA_SCRIPT = """
element => {
  const inputType = (element.getAttribute('type') || '').toLowerCase();
  const sensitive = inputType === 'password' || inputType === 'hidden' ||
    element.hasAttribute('hidden');
  const labels = element.labels ? Array.from(element.labels) : [];
  const labelText = labels.map(label => label.textContent || '').join(' ').trim();
  const accessibleName = element.getAttribute('aria-label') || labelText ||
    element.getAttribute('title') || element.textContent || '';
  return {
    sensitive: sensitive,
    tag: element.tagName.toLowerCase(),
    role: element.getAttribute('role') || element.tagName.toLowerCase(),
    accessibleName: accessibleName.trim()
  };
}
"""


class BrowserTool(Protocol):
    def observe(self) -> Observation:
        """Return deterministic browser state needed by the assertion evaluator."""

    def execute(self, action: BrowserAction) -> Observation:
        """Execute one validated action and describe its immediate result."""


class PlaywrightBrowserTool:
    """Playwright sync-page adapter kept independent from planner and state logic."""

    def __init__(
        self,
        page: Any,
        policy: ActionPolicy,
        max_observed_elements: int = MAX_OBSERVED_ELEMENTS,
        observation_timeout_ms: int = OBSERVATION_TIMEOUT_MS,
    ) -> None:
        if max_observed_elements < 1:
            raise ValueError("max_observed_elements must be positive.")
        self.page = page
        self.policy = policy
        self.max_observed_elements = max_observed_elements
        self.observation_timeout_ms = observation_timeout_ms

    def observe(self) -> Observation:
        return Observation(
            source="browser",
            data={
                "url": self._safe_url(),
                "title": self._safe_title(),
                "result": self._safe_result(),
                "permitted_elements": self._permitted_elements(),
                "permitted_elements_truncated": self._permitted_elements_truncated(),
            },
        )

    def _safe_url(self) -> str:
        """Retain the page location while removing credentials, query strings, and fragments."""
        try:
            parsed = urlsplit(str(self.page.url))
            host = parsed.hostname or ""
            if parsed.port is not None:
                host = "{0}:{1}".format(host, parsed.port)
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except Exception:
            return ""

    def _safe_title(self) -> str:
        try:
            return str(self.page.title())[:MAX_TITLE_CHARS]
        except Exception:
            return ""

    def _safe_result(self) -> Any:
        try:
            return self.page.locator("#result").text_content(
                timeout=self.observation_timeout_ms
            )
        except Exception:
            return None

    def _permitted_element_actions(self) -> Dict[str, List[str]]:
        """Build the inventory exclusively from policy, never from page discovery."""
        actions: Dict[str, List[str]] = {}
        for selector in self.policy.allowed_click_targets:
            actions.setdefault(selector, []).append(ActionKind.CLICK.value)
        for selector in self.policy.allowed_fill_targets:
            actions.setdefault(selector, []).append(ActionKind.FILL.value)
        return actions

    def _permitted_elements_truncated(self) -> bool:
        return len(self._permitted_element_actions()) > self.max_observed_elements

    def _permitted_elements(self) -> List[dict]:
        elements: List[dict] = []
        allowed = self._permitted_element_actions()
        for selector in sorted(allowed)[: self.max_observed_elements]:
            element = {"selector": selector, "permitted_actions": sorted(allowed[selector])}
            try:
                locator = self.page.locator(selector)
                if locator.count() != 1:
                    element["available"] = False
                    elements.append(element)
                    continue
                metadata = locator.evaluate(_METADATA_SCRIPT, timeout=self.observation_timeout_ms)
                if not isinstance(metadata, dict) or metadata.get("sensitive"):
                    # Password and hidden fields are deliberately omitted from observations.
                    continue
                element.update(
                    {
                        "available": True,
                        "tag": str(metadata.get("tag") or "")[:40],
                        "role": str(metadata.get("role") or "")[:40],
                        # Page-provided labels are untrusted metadata, not authorization input.
                        "accessible_name": str(metadata.get("accessibleName") or "")[
                            :MAX_ACCESSIBLE_NAME_CHARS
                        ],
                        "visible": bool(locator.is_visible(timeout=self.observation_timeout_ms)),
                        "enabled": bool(locator.is_enabled(timeout=self.observation_timeout_ms)),
                    }
                )
            except Exception:
                element["available"] = False
            elements.append(element)
        return elements

    def execute(self, action: BrowserAction) -> Observation:
        if action.kind is ActionKind.NAVIGATE:
            self.page.goto(action.target, wait_until="load")
        elif action.kind is ActionKind.CLICK:
            self.page.locator(action.target).click()
        elif action.kind is ActionKind.FILL:
            self.page.locator(action.target).fill(action.value)
        elif action.kind is ActionKind.INSPECT:
            return Observation(
                source="inspect",
                data={"target": action.target, "text": self.page.locator(action.target).text_content()},
            )
        elif action.kind is ActionKind.FINISH:
            return Observation(source="finish", data={})
        return Observation(source="browser_action", data={"kind": action.kind.value})
