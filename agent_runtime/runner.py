"""The explicit observe -> propose -> validate -> execute -> observe loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .actions import ActionKind, ActionPolicy, ActionValidationError, validate_action
from .browser import BrowserTool
from .planner import Planner
from .state import ExecutionState, ExecutionStatus, Observation


AssertionEvaluator = Callable[[Observation], bool]


@dataclass(frozen=True)
class ExecutionResult:
    state: ExecutionState

    def to_dict(self) -> dict:
        return {
            "objective": self.state.objective,
            "status": self.state.status.value,
            "assertion_result": self.state.assertion_result,
            "remaining_action_budget": self.state.remaining_action_budget,
            "action_history": [record.to_dict() for record in self.state.action_history],
            "observations": [observation.to_dict() for observation in self.state.observations],
            "error": self.state.error,
        }


class AgentRunner:
    def __init__(
        self,
        planner: Planner,
        browser: BrowserTool,
        policy: ActionPolicy,
        assertion_evaluator: AssertionEvaluator,
    ) -> None:
        self.planner = planner
        self.browser = browser
        self.policy = policy
        self.assertion_evaluator = assertion_evaluator

    def run(self, state: ExecutionState) -> ExecutionResult:
        state.start()
        try:
            state.add_observation(self.browser.observe())
        except Exception as error:
            state.fail("Initial observation failed: {0}".format(error))
            return ExecutionResult(state)

        while state.status is ExecutionStatus.RUNNING:
            latest = state.observations[-1]

            try:
                state.assertion_result = self.assertion_evaluator(latest)
            except Exception as error:
                state.fail("Assertion evaluation failed: {0}".format(error))
                break 
            if state.assertion_result:
                state.succeed()
                break
            if state.remaining_action_budget == 0:
                state.exhaust_budget()
                break

            try:
                action = self.planner.propose_action(state)
            except Exception as error:
                state.fail("Planner execution failed: {0}".format(error))
                break
            try:
                validate_action(action, self.policy)
            except ActionValidationError as error:
                state.fail("Action rejected: {0}".format(error))
                break

            if action.kind is ActionKind.FINISH:
                state.record_action(action, "finish requested")
                state.fail("Planner finished without a verified assertion.")
                break

            try:
                execution_observation = self.browser.execute(action)
                state.record_action(action, "executed")
                state.add_observation(execution_observation)
                state.add_observation(self.browser.observe())
            except Exception as error:
                state.record_action(action, "execution failed")
                state.fail("Browser execution failed: {0}".format(error))

        return ExecutionResult(state)
