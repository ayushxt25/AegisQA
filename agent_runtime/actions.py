"""Typed, intentionally small browser action vocabulary and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Set


class ActionKind(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    INSPECT = "inspect"
    FINISH = "finish"


@dataclass(frozen=True)
class BrowserAction:
    """A planner proposal; validation occurs before it reaches a browser."""

    kind: object
    target: Optional[str] = None
    value: Optional[str] = None

    def to_dict(self) -> dict:
        kind = self.kind.value if isinstance(self.kind, ActionKind) else str(self.kind)
        # Input values are never retained in execution logs; they could be secrets.
        value = "[REDACTED]" if kind == ActionKind.FILL.value and self.value is not None else self.value
        return {"kind": kind, "target": self.target, "value": value}


class ActionValidationError(ValueError):
    """Raised for action proposals outside the tightly scoped experiment policy."""


@dataclass(frozen=True)
class ActionPolicy:
    allowed_urls: Set[str]
    allowed_click_targets: Set[str]
    allowed_fill_targets: Set[str]
    allowed_inspect_targets: Set[str]


def validate_action(action: BrowserAction, policy: ActionPolicy) -> None:
    """Reject unknown tools, arbitrary selectors, values, and navigation targets."""
    if not isinstance(action.kind, ActionKind):
        raise ActionValidationError("Unsupported tool: {0!r}".format(action.kind))

    if action.kind is ActionKind.NAVIGATE:
        if action.target not in policy.allowed_urls or action.value is not None:
            raise ActionValidationError("Unauthorized or invalid navigation action.")
    elif action.kind is ActionKind.CLICK:
        if action.target not in policy.allowed_click_targets or action.value is not None:
            raise ActionValidationError("Invalid or unsafe click action.")
    elif action.kind is ActionKind.FILL:
        if action.target not in policy.allowed_fill_targets or not isinstance(action.value, str):
            raise ActionValidationError("Invalid or unsafe fill action.")
    elif action.kind is ActionKind.INSPECT:
        if action.target not in policy.allowed_inspect_targets or action.value is not None:
            raise ActionValidationError("Invalid inspect action.")
    elif action.kind is ActionKind.FINISH:
        if action.target is not None or action.value is not None:
            raise ActionValidationError("Finish action must not include arguments.")
