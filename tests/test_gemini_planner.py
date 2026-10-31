"""Mocked tests for Gemini structured-output planning; no network calls occur."""

import json
import os
import unittest

from agent_runtime.actions import ActionKind, ActionPolicy
from agent_runtime.gemini_planner import GeminiPlanner, PlannerOutputError, PlannerProviderError
from agent_runtime.state import ExecutionState, Observation


POLICY = ActionPolicy(
    allowed_urls={"file:///fixture/index.html"}, allowed_click_targets={"#increment"},
    allowed_fill_targets={"#name"}, allowed_inspect_targets={"#result"},
)


class FakeUsage:
    prompt_token_count = 12
    candidates_token_count = 5


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = FakeUsage()


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response, error)


def state():
    execution_state = ExecutionState("Reach Clicks: 1")
    execution_state.start()
    execution_state.add_observation(Observation("browser", {"result": "Clicks: 0"}))
    return execution_state


class GeminiPlannerTest(unittest.TestCase):
    def planner(self, response=None, error=None, max_calls=3):
        return GeminiPlanner(POLICY, FakeClient(response, error), {"schema": "test"}, max_model_calls=max_calls)

    def test_structured_single_action_and_compact_context(self):
        planner = self.planner(FakeResponse('{"kind":"click","target":"#increment","value":null}'))
        action = planner.propose_action(state())
        self.assertEqual(action.kind, ActionKind.CLICK)
        request = planner.client.models.calls[0]
        context = json.loads(request["contents"])
        self.assertEqual(context["remaining_action_budget"], 10)
        self.assertEqual(context["permitted_action_vocabulary"]["click"], ["#increment"])
        self.assertEqual(planner.inference_metadata.input_tokens, 12)
        self.assertEqual(planner.inference_metadata.output_tokens, 5)

    def test_malformed_or_multiple_action_output_is_rejected(self):
        planner = self.planner(FakeResponse('[{"kind":"finish","target":null,"value":null}]'))
        with self.assertRaises(PlannerOutputError):
            planner.propose_action(state())
        self.assertIn("malformed_output", planner.inference_metadata.provider_errors)

    def test_empty_response_is_rejected(self):
        planner = self.planner(FakeResponse(""))
        with self.assertRaises(PlannerOutputError):
            planner.propose_action(state())
        self.assertIn("empty_or_refused_response", planner.inference_metadata.provider_errors)

    def test_rate_limit_and_timeout_are_safe_provider_errors(self):
        rate_error = type("RateError", (Exception,), {"code": 429})()
        planner = self.planner(error=rate_error)
        with self.assertRaises(PlannerProviderError):
            planner.propose_action(state())
        self.assertEqual(planner.inference_metadata.provider_errors, ["rate_limited"])
        timeout_planner = self.planner(error=TimeoutError())
        with self.assertRaises(PlannerProviderError):
            timeout_planner.propose_action(state())
        self.assertEqual(timeout_planner.inference_metadata.provider_errors, ["timeout"])

    def test_unavailable_model_and_missing_key_are_handled(self):
        unavailable_error = type("ModelError", (Exception,), {"code": 404})()
        planner = self.planner(error=unavailable_error)
        with self.assertRaises(PlannerProviderError):
            planner.propose_action(state())
        self.assertEqual(planner.inference_metadata.provider_errors, ["model_unavailable"])
        previous_key = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(PlannerProviderError):
                GeminiPlanner.from_environment(POLICY)
        finally:
            if previous_key is not None:
                os.environ["GEMINI_API_KEY"] = previous_key

    def test_model_call_limit_is_enforced_without_retrying(self):
        planner = self.planner(FakeResponse('{"kind":"finish","target":null,"value":null}'), max_calls=1)
        planner.propose_action(state())
        with self.assertRaises(PlannerProviderError):
            planner.propose_action(state())
        self.assertEqual(len(planner.client.models.calls), 1)

    def test_planning_context_includes_navigation_prerequisites(self):
        planner = self.planner(
            FakeResponse(
                '{"kind":"navigate","target":"file:///fixture/index.html","value":null}'
            )
        )

        planner.propose_action(state())

        request = planner.client.models.calls[0]
        context = json.loads(request["contents"])

        rules = " ".join(context["planning_rules"])

        self.assertIn("about:blank", rules)
        self.assertIn("available, visible, and enabled", rules)
        self.assertIn("Never invent selectors", rules)
        self.assertEqual(context["remaining_action_budget"], 10)