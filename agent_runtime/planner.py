"""Provider-independent planner boundary and deterministic test planner."""

from __future__ import annotations

from typing import Protocol

from .actions import ActionKind, BrowserAction
from .state import ExecutionState


class Planner(Protocol):
    """Future providers implement this boundary; no provider SDK is required here."""

    def propose_action(self, state: ExecutionState) -> BrowserAction:
        """Return one action using only the supplied execution state."""


class MockPlanner:
    """Completes the controlled local fixture objective without an LLM."""

    def __init__(self, fixture_url: str) -> None:
        self.fixture_url = fixture_url

    def propose_action(self, state: ExecutionState) -> BrowserAction:
        if not state.action_history:
            return BrowserAction(ActionKind.NAVIGATE, target=self.fixture_url)
        if len(state.action_history) == 1:
            return BrowserAction(ActionKind.CLICK, target="#increment")
        return BrowserAction(ActionKind.FINISH)
