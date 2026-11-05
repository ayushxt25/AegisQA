"""Focused real-browser checks for the P0.4C Playwright evidence collector."""

import tempfile
import unittest
from pathlib import Path

try:
    import playwright.sync_api  # noqa: F401
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
else:
    PLAYWRIGHT_AVAILABLE = True

from failure_intelligence.browser_collector import ARTIFACT_ROOT, PROJECT_ROOT, collect_browser_evidence
from failure_intelligence.contracts import EvidenceSource, FailureStage
from failure_intelligence.manifests import ExecutionManifest, F05RepeatPlan, ManifestActionPolicy, ScenarioId


def attempts(prefix):
    return tuple("{0}_{1:02d}".format(prefix, number) for number in range(1, 11))


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


if __name__ == "__main__":
    unittest.main()
