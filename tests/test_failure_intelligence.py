"""Focused tests for P0.4B.1 evidence contracts and integrity."""

from datetime import datetime, timezone
import unittest

from failure_intelligence.contracts import (
    ConfidenceLevel, EvidenceIdFactory, EvidenceRecord, EvidenceSource, FailureCategory, FailureEvent,
    FailureStage, InvestigationFinding, ReproducibilityStatus, SanitizationError,
    SanitizationStatus, sanitize_observed_facts,
)
from failure_intelligence.classifier import (
    DeterministicClassifier,
    InjectedCondition,
    RepeatedAttempt,
    TrustedAssertionContract,
    TrustedInfrastructureContext,
)
from failure_intelligence.synthetic_evaluation import (
    build_adversarial_scenarios,
    build_scenarios,
    classify_scenario,
    evaluate_scenarios,
    normalized_evaluation_json,
)


FIXED_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class FailureIntelligenceContractsTest(unittest.TestCase):
    def test_serialization_is_deterministic(self):
        factory = EvidenceIdFactory(run_id="run_test", clock=lambda: FIXED_TIME)
        record = factory.create(EvidenceSource.OBSERVATION, "browser", {"title": "Fixture", "result": "Clicks: 0"})
        self.assertEqual(record.normalized_json(), record.normalized_json())
        self.assertIn('"schema_version":"1.0.0"', record.normalized_json())

    def test_ids_are_stable_sequential_and_timestamp_is_injected(self):
        factory = EvidenceIdFactory(run_id="run_abc", clock=lambda: FIXED_TIME)
        first = factory.create(EvidenceSource.ACTION, "runner", {"action_kind": "click"})
        second = factory.create(EvidenceSource.ACTION, "runner", {"action_kind": "finish"})
        self.assertEqual(first.evidence_id, "ev_run_abc_000001")
        self.assertEqual(second.evidence_id, "ev_run_abc_000002")
        self.assertEqual(first.to_dict()["captured_at"], "2026-11-01T12:00:00.000000Z")

    def test_sanitization_bounds_and_removes_url_query(self):
        facts = sanitize_observed_facts({"url": "https://example.test/path?secret=bad#fragment", "title": "x" * 300,
            "permitted_elements": [{"selector": "#increment", "visible": True, "enabled": True}]})
        self.assertEqual(facts["url"], "https://example.test/path")
        self.assertEqual(facts["title"], "[untrusted_text_omitted]")
        self.assertEqual(facts["permitted_elements"][0]["selector"], "#increment")

    def test_sensitive_and_non_allowlisted_fields_are_rejected(self):
        with self.assertRaises(SanitizationError):
            sanitize_observed_facts({"authorization_header": "Bearer value"})
        with self.assertRaises(SanitizationError):
            sanitize_observed_facts({"response_body": "<html>secret</html>"})
        with self.assertRaises(SanitizationError):
            sanitize_observed_facts({"unknown": "value"})

    def test_free_text_is_omitted_and_raw_error_messages_are_rejected(self):
        facts = sanitize_observed_facts({"result": "Bearer secret-value", "title": "password=secret"})
        self.assertNotIn("secret", str(facts))
        with self.assertRaises(SanitizationError) as error:
            sanitize_observed_facts({"error": "Bearer secret-value"})
        self.assertNotIn("secret-value", str(error.exception))
        self.assertEqual(sanitize_observed_facts({"error": "timeout"})["error"], "timeout")

    def test_evidence_facts_are_deeply_immutable_and_exports_are_copies(self):
        factory = EvidenceIdFactory(run_id="run_immutable", clock=lambda: FIXED_TIME)
        record = factory.create(EvidenceSource.OBSERVATION, "browser", {
            "permitted_elements": [{"selector": "#increment", "visible": True, "enabled": True}]
        })
        with self.assertRaises(TypeError):
            record.observed_facts["new"] = "value"
        with self.assertRaises(TypeError):
            record.observed_facts["permitted_elements"][0]["visible"] = False
        exported = record.to_dict()
        exported["observed_facts"]["permitted_elements"][0]["visible"] = False
        self.assertTrue(record.observed_facts["permitted_elements"][0]["visible"])
        self.assertTrue(record.verify_digest())

    def test_digest_verification_detects_tampering(self):
        factory = EvidenceIdFactory(run_id="run_digest", clock=lambda: FIXED_TIME)
        record = factory.create(EvidenceSource.OBSERVATION, "browser", {"status": "before"})
        self.assertTrue(record.verify_digest())
        altered = EvidenceRecord(evidence_id=record.evidence_id, run_id=record.run_id, source=record.source,
            producing_component=record.producing_component, captured_at=record.captured_at,
            observed_facts={"status": "after"}, sanitization_status=SanitizationStatus.SANITIZED,
            content_digest=record.content_digest)
        self.assertFalse(altered.verify_digest())

    def test_invalid_contract_values_are_rejected(self):
        with self.assertRaises(TypeError):
            EvidenceRecord("ev_1", "run_1", "observation", "browser", FIXED_TIME, {"result": "x"})
        with self.assertRaises(ValueError):
            InvestigationFinding("find_1", FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED,
                (), (), (), "", (), ReproducibilityStatus.NOT_ATTEMPTED)
        with self.assertRaises(ValueError):
            EvidenceRecord("ev_1", "run_1", EvidenceSource.OBSERVATION, "browser",
                datetime(2026, 11, 1, 12, 0), {"result": "x"})

    def test_mapping_and_serialized_size_limits_are_rejected(self):
        too_many_fields = {
            "action_kind": "click", "available": True, "duration_ms": 1, "enabled": True,
            "http_status": 200, "injected_delay_ms": 0, "outcome": "executed", "result": "x",
            "role": "button", "selector": "#a", "status": "ok", "tag": "button",
            "target": "#a", "timeout_ms": 10, "title": "x", "url": "https://example.test",
            "visible": True,
        }
        with self.assertRaises(SanitizationError):
            sanitize_observed_facts(too_many_fields)
        oversized = {"permitted_elements": [
            {"selector": "#" + ("x" * 255), "visible": True, "enabled": True}
            for _ in range(20)
        ]}
        with self.assertRaises(SanitizationError):
            sanitize_observed_facts(oversized)

    def test_equivalent_insertion_orders_have_equal_serialization_and_digest(self):
        left = EvidenceRecord("ev_order", "run_order", EvidenceSource.OBSERVATION, "browser", FIXED_TIME,
            {"status": "ok", "selector": "#increment", "available": True}).finalized()
        right = EvidenceRecord("ev_order", "run_order", EvidenceSource.OBSERVATION, "browser", FIXED_TIME,
            {"available": True, "selector": "#increment", "status": "ok"}).finalized()
        self.assertEqual(left.normalized_json(), right.normalized_json())
        self.assertEqual(left.content_digest, right.content_digest)


class DeterministicClassifierTest(unittest.TestCase):
    def setUp(self):
        self.classifier = DeterministicClassifier()

    def evidence(self, evidence_id, source, component, facts, run_id="run_classify"):
        return EvidenceRecord(evidence_id, run_id, source, component, FIXED_TIME, facts).finalized()

    def event(self, evidence_ids, run_id="run_classify", initial=None):
        return FailureEvent("fail_1", run_id, FIXED_TIME, FailureStage.EXECUTION,
            {"status": "failed"}, tuple(evidence_ids), initial)

    def test_r01_missing_invalid_and_mismatched_evidence_precedes_other_rules(self):
        validation = self.evidence("ev_validation", EvidenceSource.ACTION, "runner", {"error": "validation_rejected"})
        missing = self.classifier.classify(self.event(("ev_missing",)), (validation,))
        self.assertEqual((missing.applied_rule_id, missing.finding.classification), ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
        mismatch = self.classifier.classify(self.event(("ev_other",)), (self.evidence("ev_other", EvidenceSource.ACTION, "runner", {"error": "validation_rejected"}, "other_run"),))
        self.assertEqual(mismatch.applied_rule_id, "R01")
        corrupted = EvidenceRecord("ev_bad", "run_classify", EvidenceSource.ACTION, "runner", FIXED_TIME,
            {"error": "timeout"}, content_digest=validation.content_digest)
        invalid = self.classifier.classify(self.event(("ev_bad",)), (corrupted,))
        self.assertEqual(invalid.applied_rule_id, "R01")

    def test_r02_verified_action_validation_rejection_and_contradictory_reference(self):
        rejected = self.evidence("ev_rejected", EvidenceSource.ACTION, "runner", {"error": "validation_rejected"})
        result = self.classifier.classify(self.event((rejected.evidence_id,)), (rejected,))
        self.assertEqual(result.applied_rule_id, "R02")
        self.assertEqual(result.finding.classification, FailureCategory.AUTOMATION_FAILURE)
        self.assertEqual(result.finding.confidence, ConfidenceLevel.VERIFIED)
        self.assertEqual(result.finding.contradictory_evidence_ids, ())

    def test_contradictory_validation_and_execution_is_insufficient_evidence(self):
        rejected = self.evidence("ev_rejected", EvidenceSource.ACTION, "runner", {"error": "validation_rejected"})
        executed = self.evidence("ev_executed", EvidenceSource.ACTION, "runner", {"outcome": "executed"})
        result = self.classifier.classify(self.event((rejected.evidence_id, executed.evidence_id)), (rejected, executed))
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
        self.assertEqual(result.finding.contradictory_evidence_ids, (executed.evidence_id,))

    def test_r03_verified_service_failure_and_timeout_non_proof(self):
        service = self.evidence("ev_503", EvidenceSource.NETWORK, "http_server", {"http_status": 503})
        result = self.classifier.classify(self.event((service.evidence_id,)), (service,))
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R03", FailureCategory.INFRASTRUCTURE_FAILURE))
        self.assertEqual(result.finding.confidence, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE)
        timeout = self.evidence("ev_timeout", EvidenceSource.ACTION, "runner", {"error": "timeout"})
        timeout_result = self.classifier.classify(self.event((timeout.evidence_id,)), (timeout,))
        self.assertEqual((timeout_result.applied_rule_id, timeout_result.finding.classification), ("R06", FailureCategory.INSUFFICIENT_EVIDENCE))

    def test_r03_requires_permitted_producer_and_trusted_injected_context_for_verified(self):
        invalid = self.evidence("ev_invalid_503", EvidenceSource.NETWORK, "untrusted_proxy", {"http_status": 503})
        invalid_result = self.classifier.classify(self.event((invalid.evidence_id,)), (invalid,))
        self.assertEqual((invalid_result.applied_rule_id, invalid_result.finding.classification), ("R06", FailureCategory.INSUFFICIENT_EVIDENCE))
        trusted = self.evidence("ev_trusted_503", EvidenceSource.NETWORK, "http_server", {"http_status": 503})
        context = TrustedInfrastructureContext("context_f04", "run_classify", InjectedCondition.SIMULATED_HTTP_503)
        trusted_result = self.classifier.classify(self.event((trusted.evidence_id,)), (trusted,), infrastructure_context=context)
        self.assertEqual(trusted_result.finding.confidence, ConfidenceLevel.VERIFIED)

    def test_r04_requires_trusted_contract_completed_action_and_failed_assertion(self):
        action = self.evidence("ev_action", EvidenceSource.ACTION, "runner", {"outcome": "executed", "action_kind": "click"})
        assertion = self.evidence("ev_assertion", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        event = self.event((action.evidence_id, assertion.evidence_id), initial=FailureCategory.APPLICATION_DEFECT)
        contract = TrustedAssertionContract("contract_1", "run_classify", True)
        result = self.classifier.classify(event, (action, assertion), contract)
        self.assertEqual((result.applied_rule_id, result.finding.classification, result.finding.confidence),
            ("R04", FailureCategory.APPLICATION_DEFECT, ConfidenceLevel.VERIFIED))
        untrusted = self.classifier.classify(event, (action, assertion), TrustedAssertionContract("contract_2", "other_run", True))
        self.assertEqual((untrusted.applied_rule_id, untrusted.finding.classification), ("R06", FailureCategory.INSUFFICIENT_EVIDENCE))

    def test_conflicting_assertion_outcomes_prevent_application_defect(self):
        action = self.evidence("ev_action", EvidenceSource.ACTION, "runner", {"outcome": "executed"})
        failed = self.evidence("ev_failed", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        passed = self.evidence("ev_passed", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        contract = TrustedAssertionContract("contract_conflict", "run_classify", True)
        result = self.classifier.classify(self.event((action.evidence_id, failed.evidence_id, passed.evidence_id)), (action, failed, passed), contract)
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
        self.assertEqual(result.finding.contradictory_evidence_ids, (passed.evidence_id,))

    def test_r05_requires_equivalent_inconsistent_repeated_attempts(self):
        first = self.evidence("ev_first", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        second = self.evidence("ev_second", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        attempts = (RepeatedAttempt("attempt_1", "same_conditions", False, (first.evidence_id,)),
                    RepeatedAttempt("attempt_2", "same_conditions", True, (second.evidence_id,)))
        result = self.classifier.classify(self.event((first.evidence_id, second.evidence_id)), (first, second), repeated_attempts=attempts)
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R05", FailureCategory.FLAKY_OR_INTERMITTENT))
        one_attempt = self.classifier.classify(self.event((first.evidence_id,)), (first,), repeated_attempts=(attempts[0],))
        self.assertEqual((one_attempt.applied_rule_id, one_attempt.finding.classification), ("R06", FailureCategory.INSUFFICIENT_EVIDENCE))

    def test_repeated_attempt_cannot_use_same_run_evidence_outside_event(self):
        first = self.evidence("ev_first", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        outside = self.evidence("ev_outside", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        attempts = (
            RepeatedAttempt("attempt_1", "same_conditions", False, (first.evidence_id,)),
            RepeatedAttempt("attempt_2", "same_conditions", True, (outside.evidence_id,)),
        )
        result = self.classifier.classify(self.event((first.evidence_id,)), (first, outside), repeated_attempts=attempts)
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
        self.assertEqual(result.finding.confidence, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE)
        self.assertIn("repeated-attempt evidence outside event: ev_outside", result.finding.missing_evidence)
        self.assertTrue(set(result.finding.supporting_evidence_ids).issubset({first.evidence_id}))

    def test_conflicting_duplicate_evidence_ids_are_rejected_without_overwrite(self):
        failed = self.evidence("ev_duplicate", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        passed = self.evidence("ev_duplicate", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        result = self.classifier.classify(self.event((failed.evidence_id,)), (failed, passed))
        self.assertEqual((result.applied_rule_id, result.finding.classification), ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
        self.assertEqual(result.finding.confidence, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE)
        self.assertIn("ambiguous duplicate evidence ID: ev_duplicate", result.finding.missing_evidence)
        self.assertEqual(result.finding.supporting_evidence_ids, ())

    def test_repeated_attempts_are_event_scoped_and_deterministic_by_input_order(self):
        first = self.evidence("ev_first", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        second = self.evidence("ev_second", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        attempts = (
            RepeatedAttempt("attempt_1", "same_conditions", False, (first.evidence_id,)),
            RepeatedAttempt("attempt_2", "same_conditions", True, (second.evidence_id,)),
        )
        event = self.event((first.evidence_id, second.evidence_id))
        left = self.classifier.classify(event, (first, second), repeated_attempts=attempts)
        right = self.classifier.classify(event, (second, first), repeated_attempts=attempts)
        self.assertEqual((left.applied_rule_id, left.finding.classification), ("R05", FailureCategory.FLAKY_OR_INTERMITTENT))
        self.assertEqual(left, right)
        self.assertEqual(set(left.finding.supporting_evidence_ids), {first.evidence_id, second.evidence_id})

    def test_repeated_attempt_rejects_mismatch_duplicates_and_conflicting_evidence(self):
        failed = self.evidence("ev_failed", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        passed = self.evidence("ev_passed", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
        event = self.event((failed.evidence_id, passed.evidence_id))
        mismatch = (RepeatedAttempt("attempt_1", "same", True, (failed.evidence_id,)),)
        mismatch_result = self.classifier.classify(event, (failed, passed), repeated_attempts=mismatch)
        self.assertEqual(mismatch_result.applied_rule_id, "R01")
        duplicate = (RepeatedAttempt("attempt_1", "same", False, (failed.evidence_id,)),
                     RepeatedAttempt("attempt_1", "same", True, (passed.evidence_id,)))
        duplicate_result = self.classifier.classify(event, (failed, passed), repeated_attempts=duplicate)
        self.assertEqual(duplicate_result.applied_rule_id, "R01")
        conflicting = (RepeatedAttempt("attempt_1", "same", False, (failed.evidence_id, passed.evidence_id)),)
        conflicting_result = self.classifier.classify(event, (failed, passed), repeated_attempts=conflicting)
        self.assertEqual(conflicting_result.applied_rule_id, "R01")

    def test_classification_is_deterministic_for_equivalent_evidence_order(self):
        action = self.evidence("ev_a", EvidenceSource.ACTION, "runner", {"outcome": "executed"})
        assertion = self.evidence("ev_b", EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
        event = self.event((action.evidence_id, assertion.evidence_id))
        contract = TrustedAssertionContract("contract_deterministic", "run_classify", True)
        left = self.classifier.classify(event, (action, assertion), contract)
        right = self.classifier.classify(event, (assertion, action), contract)
        self.assertEqual(left, right)


class SyntheticEvaluationTest(unittest.TestCase):
    def test_scenario_construction_has_all_six_execution_only_inputs(self):
        scenarios = build_scenarios()
        self.assertEqual(tuple(item.scenario_id for item in scenarios), ("F01", "F02", "F03", "F04", "F05", "F06"))
        self.assertTrue(all(not hasattr(item, "expected_category") for item in scenarios))
        self.assertIsNotNone(scenarios[2].assertion_contract)
        self.assertIsNotNone(scenarios[3].infrastructure_context)
        self.assertEqual(len(scenarios[4].repeated_attempts), 2)
        self.assertEqual(scenarios[5].evidence, ())

    def test_ground_truth_is_joined_only_after_classifier_execution(self):
        scenario = build_scenarios()[2]
        result = classify_scenario(scenario)
        self.assertEqual(result.finding.classification, FailureCategory.APPLICATION_DEFECT)
        report = evaluate_scenarios()
        self.assertEqual(report["results"][2]["scenario_id"], "F03")
        self.assertTrue(report["results"][2]["category_match"])

    def test_evaluation_metrics_are_measured_from_classifier_results(self):
        report = evaluate_scenarios()
        self.assertEqual(report["metrics"], {
            "accuracy": 1.0,
            "confidence_level_agreement": 1.0,
            "false_application_defect_count": 0,
            "insufficient_evidence_handling": {"correct": 1, "expected": 1, "rate": 1.0},
            "scenario_count": 6,
        })

    def test_normalized_evaluation_output_is_deterministic(self):
        scenarios = build_scenarios()
        self.assertEqual(normalized_evaluation_json(scenarios), normalized_evaluation_json(reversed(scenarios)))

    def test_adversarial_evidence_cannot_produce_verified_application_defect(self):
        scenarios = build_adversarial_scenarios()
        self.assertEqual(len(scenarios), 6)
        for scenario in scenarios:
            result = classify_scenario(scenario)
            self.assertFalse(
                result.finding.classification is FailureCategory.APPLICATION_DEFECT
                and result.finding.confidence is ConfidenceLevel.VERIFIED,
                scenario.scenario_id,
            )
            self.assertEqual(result.applied_rule_id, "R01")
