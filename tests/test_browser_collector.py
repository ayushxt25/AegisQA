"""Focused real-browser checks for the P0.4C Playwright evidence collector."""

from http.client import HTTPConnection
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import playwright.sync_api  # noqa: F401
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
else:
    PLAYWRIGHT_AVAILABLE = True

from failure_intelligence.browser_collector import (
    ARTIFACT_ROOT,
    BrowserCollectionError,
    F05EvaluationResult,
    F05TrialMeasurement,
    F06EvaluationResult,
    PROJECT_ROOT,
    Loopback503Server,
    collect_browser_evidence,
    collect_f05_repeated_trials,
    collect_f06_ambiguous_evidence,
)
from failure_intelligence.classifier import DeterministicClassifier
from failure_intelligence.contracts import (
    ConfidenceLevel,
    EvidenceRecord,
    EvidenceSource,
    FailureCategory,
    FailureEvent,
    FailureStage,
)
from failure_intelligence.manifests import ExecutionManifest, F05RepeatPlan, ManifestActionPolicy, ScenarioId


def attempts(prefix):
    return tuple("{0}_{1:02d}".format(prefix, number) for number in range(1, 11))


class CollectorResultSerializationTest(unittest.TestCase):
    """Result dataclasses must retain independent, callable serializers."""

    def test_f05_and_f06_results_have_their_own_to_dict_methods(self):
        captured_at = datetime(2026, 11, 6, tzinfo=timezone.utc)
        record = EvidenceRecord(
            evidence_id="ev_result_000001", run_id="run_result", source=EvidenceSource.ACTION,
            producing_component="runner", captured_at=captured_at,
            observed_facts={"outcome": "executed"},
        ).finalized()
        event = FailureEvent(
            failure_id="failure_result", run_id="run_result", occurred_at=captured_at,
            stage=FailureStage.ASSERTION, observed_facts={"status": "assertion_failed"},
            evidence_ids=(record.evidence_id,),
        )
        classification = DeterministicClassifier().classify(event, (record,))
        trial = F05TrialMeasurement("attempt_01", 25, 1, 100, False, record.evidence_id)
        f05 = F05EvaluationResult(
            event, event, (record,), (record,), (trial,), (trial,), (), classification,
        )
        f06 = F06EvaluationResult(
            event, (record,), "ev_result_000002", Path("artifacts/p0.4c/run_result"), classification,
        )

        f05_payload = f05.to_dict()
        f06_payload = f06.to_dict()

        self.assertEqual(f05_payload["control"]["metrics"]["attempts"], 1)
        self.assertEqual(f05_payload["boundary"]["trials"][0]["attempt_id"], "attempt_01")
        self.assertEqual(f06_payload["missing_assertion_evidence_id"], "ev_result_000002")
        self.assertEqual(f06_payload["run_id"], "run_result")


class Loopback503ServerTest(unittest.TestCase):
    def test_server_binds_to_loopback_and_returns_only_the_configured_503_route(self):
        try:
            server_context = Loopback503Server("/service-unavailable")
        except BrowserCollectionError:
            self.skipTest("Loopback sockets are unavailable in this environment")
        with server_context as server:
            host, port = server.server.server_address[:2]
            self.assertEqual(host, "127.0.0.1")
            connection = HTTPConnection(host, port, timeout=1)
            try:
                connection.request("GET", "/service-unavailable")
                response = connection.getresponse()
                self.assertEqual(response.status, 503)
                response.close()
                connection.request("GET", "/not-configured")
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.close()
            finally:
                connection.close()


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright is not installed")
class BrowserCollectorTest(unittest.TestCase):
    def manifest(self, scenario_id, fixture_path, run_id, repeat_plan=None):
        fixture_url = (PROJECT_ROOT / fixture_path).resolve().as_uri()
        return ExecutionManifest(
            scenario_id=scenario_id,
            run_id=run_id,
            artifact_directory="artifacts/p0.4c/{0}".format(run_id),
            action_policy=ManifestActionPolicy(
                allowed_urls=(fixture_url,), allowed_click_targets=("#increment",),
                allowed_inspect_targets=("#result",),
            ),
            timeout_ms=1_000,
            fixture_path=fixture_path,
            f05_repeat_plan=repeat_plan,
        )

    def f04_manifest(self, run_id):
        return ExecutionManifest(
            scenario_id=ScenarioId.F04,
            run_id=run_id,
            artifact_directory="artifacts/p0.4c/{0}".format(run_id),
            action_policy=ManifestActionPolicy(allowed_urls=(), allowed_click_targets=()),
            timeout_ms=1_000,
            server_route="/service-unavailable",
        )

    def collect(self, scenario_id, fixture_path, repeat_plan=None):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="collector-", dir=str(ARTIFACT_ROOT)) as temporary_directory:
            run_id = Path(temporary_directory).name
            result = collect_browser_evidence(
                self.manifest(scenario_id, fixture_path, run_id, repeat_plan), PROJECT_ROOT
            )
            self.assertTrue((result.artifact_directory / "evidence.json").is_file())
            self.assertTrue((result.artifact_directory / "failure-event.json").is_file())
            return result, (result.artifact_directory / "evidence.json").read_text(encoding="utf-8")

    def test_f01_collects_static_validation_rejection_without_page_text(self):
        result, serialized = self.collect(ScenarioId.F01, "fixture/index.html")
        self.assertEqual(result.failure_event.stage, FailureStage.VALIDATION)
        self.assertIsNone(result.assertion_passed)
        self.assertEqual([record.source for record in result.evidence], [EvidenceSource.OBSERVATION, EvidenceSource.ACTION])
        self.assertEqual(result.evidence[-1].observed_facts["error"], "validation_rejected")
        self.assertNotIn("AegisQA browser runtime fixture", serialized)
        self.assertNotIn("Clicks: 0", serialized)

    def test_f02_collects_runtime_validation_rejection_for_disabled_button(self):
        result, _ = self.collect(ScenarioId.F02, "fixture/p0.4c/disabled-button.html")
        element = result.evidence[0].observed_facts["permitted_elements"][0]
        self.assertTrue(element["available"])
        self.assertTrue(element["visible"])
        self.assertFalse(element["enabled"])
        self.assertEqual(result.evidence[-1].observed_facts["error"], "validation_rejected")
        self.assertEqual(result.failure_event.evidence_ids, tuple(record.evidence_id for record in result.evidence))

    def test_f03_collects_executed_action_and_failed_independent_assertion(self):
        result, serialized = self.collect(ScenarioId.F03, "fixture/p0.4c/incorrect-result.html")
        self.assertEqual(result.failure_event.stage, FailureStage.ASSERTION)
        self.assertFalse(result.assertion_passed)
        self.assertEqual(result.evidence[-2].observed_facts["outcome"], "executed")
        self.assertEqual(result.evidence[-1].observed_facts["status"], "assertion_failed")
        self.assertTrue(all(record.verify_digest() for record in result.evidence))
        self.assertNotIn("Incorrect result scenario", serialized)
        self.assertNotIn("Clicks: 0", serialized)

    def test_f05_collects_one_control_profile_action_without_repeat_evaluation(self):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="collector-", dir=str(ARTIFACT_ROOT)) as temporary_directory:
            run_id = Path(temporary_directory).name
            plan = F05RepeatPlan(
                run_id, "control_run", attempts("boundary"), attempts("control"), "boundary_profile_v1",
            )
            result = collect_browser_evidence(
                self.manifest(ScenarioId.F05, "fixture/p0.4c/delayed-result.html", run_id, plan), PROJECT_ROOT
            )
            self.assertTrue(result.assertion_passed)
            self.assertEqual(result.evidence[-1].observed_facts["status"], "assertion_passed")
            self.assertEqual(len(result.evidence), 3)

    def test_f04_loopback_503_collects_network_evidence_and_classifies_with_context(self):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="collector-", dir=str(ARTIFACT_ROOT)) as temporary_directory:
            run_id = Path(temporary_directory).name
            result = collect_browser_evidence(self.f04_manifest(run_id), PROJECT_ROOT)
            network = result.evidence[0]
            self.assertEqual(result.failure_event.stage, FailureStage.ENVIRONMENT)
            self.assertEqual((network.source, network.producing_component), (EvidenceSource.NETWORK, "http_server"))
            self.assertEqual(network.observed_facts["http_status"], 503)
            self.assertTrue(network.observed_facts["url"].startswith("http://127.0.0.1:"))
            self.assertTrue(network.verify_digest())
            self.assertIsNotNone(result.infrastructure_context)
            classified = DeterministicClassifier().classify(
                result.failure_event, result.evidence, infrastructure_context=result.infrastructure_context,
            )
            self.assertEqual(
                (classified.applied_rule_id, classified.finding.classification, classified.finding.confidence),
                ("R03", FailureCategory.INFRASTRUCTURE_FAILURE, ConfidenceLevel.VERIFIED),
            )
            serialized = (result.artifact_directory / "evidence.json").read_text(encoding="utf-8")
            self.assertNotIn("Content-Length", serialized)
            self.assertNotIn("Server:", serialized)

    def test_f05_repeat_protocol_keeps_groups_isolated_and_uses_actual_outcomes(self):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="boundary-", dir=str(ARTIFACT_ROOT)) as boundary_directory:
            with tempfile.TemporaryDirectory(prefix="control-", dir=str(ARTIFACT_ROOT)) as control_directory:
                boundary_run_id = Path(boundary_directory).name
                control_run_id = Path(control_directory).name
                plan = F05RepeatPlan(
                    boundary_run_id, control_run_id, attempts("boundary"), attempts("control"), "boundary_profile_v1",
                )
                result = collect_f05_repeated_trials(
                    self.manifest(ScenarioId.F05, "fixture/p0.4c/delayed-result.html", boundary_run_id, plan),
                    PROJECT_ROOT,
                )
                self.assertEqual(len(result.control_trials), 10)
                self.assertEqual(len(result.boundary_trials), 10)
                self.assertNotEqual(result.control_event.run_id, result.boundary_event.run_id)
                self.assertEqual({record.run_id for record in result.boundary_evidence}, {boundary_run_id})
                self.assertEqual({record.run_id for record in result.control_evidence}, {control_run_id})
                self.assertEqual(len({record.evidence_id for record in result.boundary_evidence}), len(result.boundary_evidence))
                self.assertEqual(
                    [record.evidence_id for record in result.boundary_evidence],
                    ["ev_{0}_{1:06d}".format(boundary_run_id, index)
                     for index in range(1, len(result.boundary_evidence) + 1)],
                )
                self.assertEqual(set(result.boundary_event.evidence_ids),
                                 {record.evidence_id for record in result.boundary_evidence})
                self.assertTrue(all(trial.assertion_duration_ms is not None for trial in result.boundary_trials))
                self.assertEqual(len(result.repeated_attempts), 10)
                self.assertEqual(
                    {attempt.evidence_ids[0] for attempt in result.repeated_attempts},
                    {trial.assertion_evidence_id for trial in result.boundary_trials},
                )
                self.assertTrue((Path(boundary_directory) / "f05-results.json").is_file())
                self.assertTrue((Path(control_directory) / "f05-results.json").is_file())

    def test_f06_real_execution_with_unavailable_post_action_assertion_is_insufficient(self):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="f06-", dir=str(ARTIFACT_ROOT)) as temporary_directory:
            run_id = Path(temporary_directory).name
            result = collect_f06_ambiguous_evidence(
                self.manifest(ScenarioId.F06, "fixture/index.html", run_id), PROJECT_ROOT
            )
            self.assertEqual((result.classification.applied_rule_id, result.classification.finding.classification),
                             ("R01", FailureCategory.INSUFFICIENT_EVIDENCE))
            self.assertEqual(result.failure_event.evidence_ids[-1], result.missing_assertion_evidence_id)
            self.assertNotIn(result.missing_assertion_evidence_id,
                             {record.evidence_id for record in result.evidence})
            self.assertTrue(result.missing_assertion_evidence_id.startswith("ev_{0}_".format(run_id)))
            self.assertEqual({record.run_id for record in result.evidence}, {run_id})
            serialized = (result.artifact_directory / "f06-results.json").read_text(encoding="utf-8")
            self.assertNotIn("Clicks: 1", serialized)
            self.assertNotIn("AegisQA browser runtime fixture", serialized)


if __name__ == "__main__":
    unittest.main()
