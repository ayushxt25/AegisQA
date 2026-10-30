"""In-memory state and explicit transition rules for one agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .actions import BrowserAction


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class Observation:
    source: str
    data: dict

    def to_dict(self) -> dict:
        return {"source": self.source, "data": self.data}


@dataclass(frozen=True)
class ActionRecord:
    action: BrowserAction
    outcome: str

    def to_dict(self) -> dict:
        return {"action": self.action.to_dict(), "outcome": self.outcome}


@dataclass
class ExecutionState:
    objective: str
    remaining_action_budget: int = 10
    action_history: List[ActionRecord] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    status: ExecutionStatus = ExecutionStatus.PENDING
    assertion_result: Optional[bool] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not 1 <= self.remaining_action_budget <= 10:
            raise ValueError("Action budget must be between 1 and 10.")

    def start(self) -> None:
        self._transition(ExecutionStatus.RUNNING)

    def add_observation(self, observation: Observation) -> None:
        if self.status is not ExecutionStatus.RUNNING:
            raise RuntimeError("Observations are only valid while running.")
        self.observations.append(observation)

    def record_action(self, action: BrowserAction, outcome: str) -> None:
        if self.status is not ExecutionStatus.RUNNING:
            raise RuntimeError("Actions are only valid while running.")
        if self.remaining_action_budget <= 0:
            raise RuntimeError("Action budget is exhausted.")
        self.action_history.append(ActionRecord(action, outcome))
        self.remaining_action_budget -= 1

    def succeed(self) -> None:
        if self.assertion_result is not True:
            raise RuntimeError("Cannot succeed without a verified assertion.")
        self._transition(ExecutionStatus.SUCCEEDED)

    def fail(self, error: str) -> None:
        self.error = error
        self._transition(ExecutionStatus.FAILED)

    def exhaust_budget(self) -> None:
        self._transition(ExecutionStatus.BUDGET_EXHAUSTED)

    def _transition(self, target: ExecutionStatus) -> None:
        allowed = {
            ExecutionStatus.PENDING: {ExecutionStatus.RUNNING},
            ExecutionStatus.RUNNING: {
                ExecutionStatus.SUCCEEDED,
                ExecutionStatus.FAILED,
                ExecutionStatus.BUDGET_EXHAUSTED,
            },
        }
        if target not in allowed.get(self.status, set()):
            raise RuntimeError(
                "Invalid state transition: {0} -> {1}".format(self.status.value, target.value)
            )
        self.status = target
