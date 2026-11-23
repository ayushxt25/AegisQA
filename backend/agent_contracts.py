"""Strict parent-side contracts for future fixture-only agent execution."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from agent_runtime.actions import ActionKind


SCHEMA_VERSION = "1.0"
FIXTURE_IDENTITY = "aegisqa-controlled-fixture"
FIXTURE_ORIGIN = "http://aegisqa-controlled-fixture:8080"
FIXTURE_URL = FIXTURE_ORIGIN + "/browser"
SCENARIOS = frozenset(("fixture_increment_v1", "fixture_assertion_failure_v1", "fixture_timeout_v1"))
MAX_MESSAGE_BYTES = 8192
MAX_ACTIONS = 10
_REQUEST_KEYS = frozenset(("schema_version", "project_id", "execution_job_id", "execution_attempt_id", "browser_target_version_id", "test_definition_version_id", "fixture_identity", "allowed_origin", "scenario_id", "action_budget", "model_call_budget", "deadline_ms", "action_policy"))
_RESULT_KEYS = frozenset(("type", "schema_version", "project_id", "execution_job_id", "execution_attempt_id", "scenario_id", "completion", "assertion_verified", "action_count", "remaining_action_budget", "duration_ms", "safe_failure_reason", "actions", "observation_summary"))
_POLICY_KEYS = frozenset(("allowed_urls", "allowed_click_targets", "allowed_fill_targets", "allowed_inspect_targets"))
_COMPLETIONS = frozenset(("assertion_passed", "assertion_failed", "automation_failed", "budget_exhausted", "finished_without_verified_assertion"))
_FAILURE_REASONS = frozenset(("action_rejected", "assertion_failed", "budget_exhausted", "execution_failed", "finish_unverified", "observation_unavailable"))


class AgentContractError(ValueError):
    """The untrusted request or child payload is outside the fixture contract."""


def _exact_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AgentContractError(field + " must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError):
        raise AgentContractError(field + " must be a UUID string") from None
    if str(parsed) != value:
        raise AgentContractError(field + " must be canonical")
    return value


def _object(value: Any, allowed: frozenset, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != allowed:
        raise AgentContractError(name + " has unsupported or missing fields")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise AgentContractError(name + " must be JSON-compatible") from None
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise AgentContractError(name + " exceeds the message limit")
    return value


def _string_list(value: Any, allowed: Tuple[str, ...], name: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AgentContractError(name + " must be a string list")
    if tuple(sorted(value)) != tuple(sorted(allowed)) or len(set(value)) != len(value):
        raise AgentContractError(name + " is not fixture-authorized")
    return tuple(value)


@dataclass(frozen=True)
class FixtureAgentRequest:
    project_id: str
    execution_job_id: str
    execution_attempt_id: str
    browser_target_version_id: str
    test_definition_version_id: str
    scenario_id: str
    action_budget: int
    deadline_ms: int
    action_policy: Dict[str, Tuple[str, ...]]

    @classmethod
    def from_dict(cls, payload: Any) -> "FixtureAgentRequest":
        value = _object(payload, _REQUEST_KEYS, "request")
        if value["schema_version"] != SCHEMA_VERSION or value["fixture_identity"] != FIXTURE_IDENTITY or value["allowed_origin"] != FIXTURE_ORIGIN:
            raise AgentContractError("request version or fixture identity is invalid")
        if value["scenario_id"] not in SCENARIOS:
            raise AgentContractError("fixture scenario is invalid")
        if not isinstance(value["action_budget"], int) or not 1 <= value["action_budget"] <= MAX_ACTIONS:
            raise AgentContractError("action budget is invalid")
        if value["model_call_budget"] != 0:
            raise AgentContractError("model calls are not enabled for fixture execution")
        if not isinstance(value["deadline_ms"], int) or not 1000 <= value["deadline_ms"] <= 30000:
            raise AgentContractError("deadline is invalid")
        policy = _object(value["action_policy"], _POLICY_KEYS, "action policy")
        normalized = {
            "allowed_urls": _string_list(policy["allowed_urls"], (FIXTURE_URL,), "allowed_urls"),
            "allowed_click_targets": _string_list(policy["allowed_click_targets"], ("#increment",), "allowed_click_targets"),
            "allowed_fill_targets": _string_list(policy["allowed_fill_targets"], (), "allowed_fill_targets"),
            "allowed_inspect_targets": _string_list(policy["allowed_inspect_targets"], (), "allowed_inspect_targets"),
        }
        return cls(*(_exact_uuid(value[key], key) for key in ("project_id", "execution_job_id", "execution_attempt_id", "browser_target_version_id", "test_definition_version_id")), value["scenario_id"], value["action_budget"], value["deadline_ms"], normalized)

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "project_id": self.project_id, "execution_job_id": self.execution_job_id, "execution_attempt_id": self.execution_attempt_id, "browser_target_version_id": self.browser_target_version_id, "test_definition_version_id": self.test_definition_version_id, "fixture_identity": FIXTURE_IDENTITY, "allowed_origin": FIXTURE_ORIGIN, "scenario_id": self.scenario_id, "action_budget": self.action_budget, "model_call_budget": 0, "deadline_ms": self.deadline_ms, "action_policy": {key: list(value) for key, value in self.action_policy.items()}}


@dataclass(frozen=True)
class FixtureAgentResult:
    completion: str
    assertion_verified: bool
    action_count: int
    remaining_action_budget: int
    duration_ms: int
    safe_failure_reason: Any
    actions: Tuple[Dict[str, str], ...]
    observation_summary: Dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Any, request: FixtureAgentRequest) -> "FixtureAgentResult":
        value = _object(payload, _RESULT_KEYS, "result")
        if value["type"] != "agent_attempt_result" or value["schema_version"] != SCHEMA_VERSION:
            raise AgentContractError("result version is invalid")
        for key, expected in (("project_id", request.project_id), ("execution_job_id", request.execution_job_id), ("execution_attempt_id", request.execution_attempt_id), ("scenario_id", request.scenario_id)):
            if value[key] != expected:
                raise AgentContractError("result identity does not match request")
        if value["completion"] not in _COMPLETIONS or not isinstance(value["assertion_verified"], bool):
            raise AgentContractError("result completion is invalid")
        if (value["completion"] == "assertion_passed") != value["assertion_verified"]:
            raise AgentContractError("result assertion state is inconsistent")
        if not isinstance(value["action_count"], int) or not 0 <= value["action_count"] <= request.action_budget:
            raise AgentContractError("result action count is invalid")
        if not isinstance(value["remaining_action_budget"], int) or value["remaining_action_budget"] != request.action_budget - value["action_count"]:
            raise AgentContractError("result remaining budget is invalid")
        if not isinstance(value["duration_ms"], int) or not 0 <= value["duration_ms"] <= request.deadline_ms:
            raise AgentContractError("result duration is invalid")
        if value["safe_failure_reason"] is not None and value["safe_failure_reason"] not in _FAILURE_REASONS:
            raise AgentContractError("result failure reason is invalid")
        if not isinstance(value["actions"], list) or len(value["actions"]) != value["action_count"]:
            raise AgentContractError("result actions are invalid")
        actions = []
        for item in value["actions"]:
            if not isinstance(item, dict) or frozenset(item) != {"kind", "outcome"} or item["kind"] not in {kind.value for kind in ActionKind} or item["outcome"] not in ("executed", "rejected", "failed", "finish_requested"):
                raise AgentContractError("result action summary is invalid")
            actions.append({"kind": item["kind"], "outcome": item["outcome"]})
        observation = value["observation_summary"]
        if not isinstance(observation, dict) or frozenset(observation) != {"current_url_approved", "permitted_element_count"} or not isinstance(observation["current_url_approved"], bool) or not isinstance(observation["permitted_element_count"], int) or not 0 <= observation["permitted_element_count"] <= 10:
            raise AgentContractError("result observation summary is invalid")
        return cls(value["completion"], value["assertion_verified"], value["action_count"], value["remaining_action_budget"], value["duration_ms"], value["safe_failure_reason"], tuple(actions), dict(observation))
