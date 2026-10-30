"""Focused unit tests for P0.2's deterministic execution infrastructure."""

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.actions import ActionKind, ActionPolicy, BrowserAction
from agent_runtime.logging import write_execution_log
from agent_runtime.planner import MockPlanner
from agent_runtime.runner import AgentRunner
from agent_runtime.state import ExecutionState, ExecutionStatus, Observation


FIXTURE_URL = "file:///controlled-fixture/index.html"
POLICY = ActionPolicy(
    allowed_urls={FIXTURE_URL},
    allowed_click_targets={"#increment"},
    allowed_fill_targets={"#name"},
    allowed_inspect_targets={"#result", "#name"},
)


def fixture_assertion(observation: Observation) -> bool:
    return observation.data.get("result") == "Clicks: 1"


class FakeBrowser:
    def __init__(self, result: str = "Clicks: 0") -> None:
        self.result = result

    def observe(self) -> Observation:
        return Observation("browser", {"result": self.result})

    def execute(self, action: BrowserAction) -> Observation:
        if action.kind is ActionKind.CLICK and action.target == "#increment":
            self.result = "Clicks: 1"
        return Observation("browser_action", {"kind": action.kind.value})


class StaticPlanner:
    def __init__(self, action: BrowserAction) -> None:
        self.action = action

    def propose_action(self, state: ExecutionState) -> BrowserAction:
        return self.action


class InspectForeverPlanner:
    def propose_action(self, state: ExecutionState) -> BrowserAction:
        return BrowserAction(ActionKind.INSPECT, target="#result")


def run(planner, browser=None, budget=10):
    return AgentRunner(planner, browser or FakeBrowser(), POLICY, fixture_assertion).run(
        ExecutionState("Reach Clicks: 1", remaining_action_budget=budget)
    )


class AgentRuntimeTest(unittest.TestCase):
    def test_successful_execution_writes_structured_log(self) -> None:
        result = run(MockPlanner(FIXTURE_URL))

        self.assertEqual(result.state.status, ExecutionStatus.SUCCEEDED)
        self.assertTrue(result.state.assertion_result)
        self.assertEqual(len(result.state.action_history), 2)
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = Path(temporary_directory) / "execution.json"
            write_execution_log(result, log_path)
            self.assertEqual(json.loads(log_path.read_text(encoding="utf-8"))["status"], "succeeded")

    def test_invalid_tool_is_rejected(self) -> None:
        result = run(StaticPlanner(BrowserAction("delete_everything")))
        self.assertEqual(result.state.status, ExecutionStatus.FAILED)
        self.assertIn("Unsupported tool", result.state.error)

    def test_unauthorized_navigation_is_rejected(self) -> None:
        result = run(StaticPlanner(BrowserAction(ActionKind.NAVIGATE, "https://example.com")))
        self.assertEqual(result.state.status, ExecutionStatus.FAILED)
        self.assertIn("Unauthorized", result.state.error)

    def test_action_budget_exhaustion_stops_loop(self) -> None:
        result = run(InspectForeverPlanner(), budget=2)
        self.assertEqual(result.state.status, ExecutionStatus.BUDGET_EXHAUSTED)
        self.assertEqual(len(result.state.action_history), 2)
        self.assertFalse(result.state.assertion_result)

    def test_failed_assertion_does_not_become_success(self) -> None:
        result = run(StaticPlanner(BrowserAction(ActionKind.FINISH)))
        self.assertEqual(result.state.status, ExecutionStatus.FAILED)
        self.assertFalse(result.state.assertion_result)

    def test_finish_without_verified_success_is_rejected(self) -> None:
        result = run(StaticPlanner(BrowserAction(ActionKind.FINISH)))
        self.assertIn("finished without a verified assertion", result.state.error)

    def test_execution_state_transitions_are_strict(self) -> None:
        state = ExecutionState("objective")
        with self.assertRaises(RuntimeError):
            state.succeed()
        state.start()
        state.assertion_result = True
        state.succeed()
        self.assertEqual(state.status, ExecutionStatus.SUCCEEDED)
        with self.assertRaises(RuntimeError):
            state.start()
