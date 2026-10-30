"""Thin browser-tool adapter; it accepts already validated actions only."""

from __future__ import annotations

from typing import Any, Protocol

from .actions import ActionKind, BrowserAction
from .state import Observation


class BrowserTool(Protocol):
    def observe(self) -> Observation:
        """Return deterministic browser state needed by the assertion evaluator."""

    def execute(self, action: BrowserAction) -> Observation:
        """Execute one validated action and describe its immediate result."""


class PlaywrightBrowserTool:
    """Playwright sync-page adapter kept independent from planner and state logic."""

    def __init__(self, page: Any) -> None:
        self.page = page

    def observe(self) -> Observation:
        if self.page.url == "about:blank":
            return Observation(source="browser", data={"result": None})
        return Observation(
            source="browser",
            data={"result": self.page.locator("#result").text_content()},
        )

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
