"""Focused tests for P0.4B.1 evidence contracts and integrity."""

from datetime import datetime, timezone
import unittest

from failure_intelligence.contracts import (
    ConfidenceLevel, EvidenceIdFactory, EvidenceRecord, EvidenceSource, FailureCategory,
    FailureStage, InvestigationFinding, ReproducibilityStatus, SanitizationError,
    SanitizationStatus, sanitize_observed_facts,
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
