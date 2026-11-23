from __future__ import annotations

import unittest
import uuid

from backend.agent_contracts import AgentContractError, FIXTURE_ORIGIN, FIXTURE_URL, FixtureAgentRequest, FixtureAgentResult


def request_payload():
    ids = [str(uuid.uuid4()) for _ in range(5)]
    return {"schema_version": "1.0", "project_id": ids[0], "execution_job_id": ids[1], "execution_attempt_id": ids[2], "browser_target_version_id": ids[3], "test_definition_version_id": ids[4], "fixture_identity": "aegisqa-controlled-fixture", "allowed_origin": FIXTURE_ORIGIN, "scenario_id": "fixture_increment_v1", "action_budget": 3, "model_call_budget": 0, "deadline_ms": 30000, "action_policy": {"allowed_urls": [FIXTURE_URL], "allowed_click_targets": ["#increment"], "allowed_fill_targets": [], "allowed_inspect_targets": []}}


def result_payload(request):
    return {"type": "agent_attempt_result", "schema_version": "1.0", "project_id": request.project_id, "execution_job_id": request.execution_job_id, "execution_attempt_id": request.execution_attempt_id, "scenario_id": request.scenario_id, "completion": "assertion_passed", "assertion_verified": True, "action_count": 2, "remaining_action_budget": 1, "duration_ms": 100, "safe_failure_reason": None, "actions": [{"kind": "navigate", "outcome": "executed"}, {"kind": "click", "outcome": "executed"}], "observation_summary": {"current_url_approved": True, "permitted_element_count": 1}}


class FixtureAgentContractTest(unittest.TestCase):
    def test_valid_round_trip(self):
        request = FixtureAgentRequest.from_dict(request_payload())
        self.assertEqual(request_payload().keys(), request.to_dict().keys())
        self.assertTrue(FixtureAgentResult.from_dict(result_payload(request), request).assertion_verified)

    def test_request_rejects_version_capability_secret_and_budget_violations(self):
        for mutate in (lambda p: p.update(schema_version="2.0"), lambda p: p.update(model_call_budget=1), lambda p: p.update(api_key="secret"), lambda p: p.update(action_budget=11), lambda p: p["action_policy"].update(allowed_urls=["https://example.com"])):
            payload = request_payload(); mutate(payload)
            with self.assertRaises(AgentContractError): FixtureAgentRequest.from_dict(payload)

    def test_result_rejects_identity_version_secret_and_budget_violations(self):
        request = FixtureAgentRequest.from_dict(request_payload())
        for mutate in (lambda p: p.update(schema_version="2.0"), lambda p: p.update(project_id=str(uuid.uuid4())), lambda p: p.update(cookie="secret"), lambda p: p.update(action_count=4), lambda p: p.update(duration_ms=30001), lambda p: p["observation_summary"].update(page_text="secret")):
            payload = result_payload(request); mutate(payload)
            with self.assertRaises(AgentContractError): FixtureAgentResult.from_dict(payload, request)

    def test_result_rejects_malformed_oversized_and_inconsistent_payloads(self):
        request = FixtureAgentRequest.from_dict(request_payload())
        with self.assertRaises(AgentContractError): FixtureAgentResult.from_dict("not-json-object", request)
        payload = result_payload(request); payload["actions"] = [{"kind": "click", "outcome": "executed"}] * 2000
        with self.assertRaises(AgentContractError): FixtureAgentResult.from_dict(payload, request)
        payload = result_payload(request); payload["assertion_verified"] = False
        with self.assertRaises(AgentContractError): FixtureAgentResult.from_dict(payload, request)
