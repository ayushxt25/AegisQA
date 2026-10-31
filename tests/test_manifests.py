"""Focused tests for the P0.4C execution/evaluation manifest boundary."""

import unittest

from failure_intelligence.contracts import ConfidenceLevel, FailureCategory
from failure_intelligence.manifests import (
    EvaluationExpectation,
    EvaluationManifest,
    ExecutionManifest,
    F05RepeatPlan,
    ManifestActionPolicy,
    ScenarioId,
)


def policy():
    return ManifestActionPolicy(
        allowed_urls=("file:///fixture/index.html",),
        allowed_click_targets=("#increment",),
        allowed_inspect_targets=("#result",),
    )


def attempts(prefix):
    return tuple("{0}_{1:02d}".format(prefix, number) for number in range(1, 11))


def expectations():
    return (
        EvaluationExpectation(ScenarioId.F01, FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F02, FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F03, FailureCategory.APPLICATION_DEFECT, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F04, FailureCategory.INFRASTRUCTURE_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F05, FailureCategory.FLAKY_OR_INTERMITTENT, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
        EvaluationExpectation(ScenarioId.F06, FailureCategory.INSUFFICIENT_EVIDENCE, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
    )


class ManifestTest(unittest.TestCase):
    def test_execution_manifests_support_each_controlled_scenario_without_ground_truth(self):
        manifests = []
        for scenario_id in ScenarioId:
            run_id = "run_{0}".format(scenario_id.value.lower())
            kwargs = {
                "scenario_id": scenario_id,
                "run_id": run_id,
                "artifact_directory": "artifacts\\p0.4c\\{0}".format(run_id),
                "action_policy": policy(),
                "timeout_ms": 1_000,
            }
            if scenario_id is ScenarioId.F04:
                kwargs["server_route"] = "/service-unavailable"
            elif scenario_id is ScenarioId.F05:
                kwargs["fixture_path"] = "fixture/p0.4c/delayed-result.html"
                kwargs["f05_repeat_plan"] = F05RepeatPlan(
                    run_id, "run_f05_control", attempts("boundary"), attempts("control"), "boundary_profile_v1",
                )
            else:
                kwargs["fixture_path"] = "fixture/index.html"
            manifests.append(ExecutionManifest(**kwargs))

        self.assertEqual([manifest.scenario_id for manifest in manifests], list(ScenarioId))
        serialized = manifests[2].to_dict()
        self.assertNotIn("expected_category", serialized)
        self.assertNotIn("expected_confidence", serialized)
        self.assertEqual(manifests[0].artifact_directory, "artifacts/p0.4c/run_f01")

    def test_evaluation_manifest_is_typed_and_separate_from_execution_inputs(self):
        evaluation = EvaluationManifest("artifacts/p0.4c/evaluation/answer-key.json", tuple(reversed(expectations())))
        self.assertEqual([item.scenario_id for item in evaluation.expectations], list(ScenarioId))
        execution = ExecutionManifest(
            ScenarioId.F01, "run_f01", "artifacts/p0.4c/run_f01", policy(), 1_000,
            fixture_path="fixture/index.html",
        )
        self.assertNotIn("expected_category", execution.normalized_json())
        self.assertIn("expected_category", evaluation.normalized_json())

    def test_f05_requires_shared_boundary_run_unique_attempts_and_separate_control_run(self):
        plan = F05RepeatPlan("run_boundary", "run_control", attempts("boundary"), attempts("control"), "profile_v1")
        manifest = ExecutionManifest(
            ScenarioId.F05, "run_boundary", "artifacts/p0.4c/run_boundary", policy(), 1_000,
            fixture_path="fixture/p0.4c/delayed-result.html", f05_repeat_plan=plan,
        )
        self.assertEqual(manifest.run_id, manifest.f05_repeat_plan.boundary_run_id)
        with self.assertRaises(ValueError):
            F05RepeatPlan("run_boundary", "run_boundary", attempts("boundary"), attempts("control"), "profile_v1")
        with self.assertRaises(ValueError):
            F05RepeatPlan("run_boundary", "run_control", ("duplicate",) * 10, attempts("control"), "profile_v1")
        with self.assertRaises(ValueError):
            ExecutionManifest(
                ScenarioId.F05, "wrong_run", "artifacts/p0.4c/wrong_run", policy(), 1_000,
                fixture_path="fixture/p0.4c/delayed-result.html", f05_repeat_plan=plan,
            )

    def test_validation_rejects_invalid_scenarios_associations_and_paths(self):
        with self.assertRaises(TypeError):
            ExecutionManifest("F01", "run_f01", "artifacts/p0.4c/run_f01", policy(), 1_000, fixture_path="fixture/index.html")
        with self.assertRaises(ValueError):
            ExecutionManifest(ScenarioId.F04, "run_f04", "artifacts/p0.4c/run_f04", policy(), 1_000, fixture_path="fixture/index.html")
        with self.assertRaises(ValueError):
            ExecutionManifest(ScenarioId.F01, "run_f01", "artifacts/outside", policy(), 1_000, fixture_path="fixture/index.html")
        with self.assertRaises(ValueError):
            EvaluationManifest("artifacts/p0.4c/evaluation/key.txt", expectations())
        with self.assertRaises(ValueError):
            EvaluationManifest("artifacts/p0.4c/evaluation/key.json", expectations()[:-1])

    def test_path_segments_and_run_ids_are_rejected_before_normalization(self):
        for artifact_path in (
            "artifacts//p0.4c/run_f01",
            "artifacts/./p0.4c/run_f01",
            "artifacts/p0.4c/../run_f01",
        ):
            with self.subTest(artifact_path=artifact_path), self.assertRaises(ValueError):
                ExecutionManifest(ScenarioId.F01, "run_f01", artifact_path, policy(), 1_000,
                                  fixture_path="fixture/index.html")
        for fixture_path in ("fixture//index.html", "fixture/./index.html", "fixture/../index.html", "fixture"):
            with self.subTest(fixture_path=fixture_path), self.assertRaises(ValueError):
                ExecutionManifest(ScenarioId.F01, "run_f01", "artifacts/p0.4c/run_f01", policy(), 1_000,
                                  fixture_path=fixture_path)
        for run_id in ("run/f01", "run\\f01"):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                ExecutionManifest(ScenarioId.F01, run_id, "artifacts/p0.4c/run_f01", policy(), 1_000,
                                  fixture_path="fixture/index.html")
        with self.assertRaises(ValueError):
            F05RepeatPlan("boundary/run", "control", attempts("boundary"), attempts("control"), "profile_v1")
        with self.assertRaises(ValueError):
            F05RepeatPlan("boundary", "control\\run", attempts("boundary"), attempts("control"), "profile_v1")

    def test_serialization_is_deterministic(self):
        left = EvaluationManifest("artifacts/p0.4c/evaluation/key.json", expectations())
        right = EvaluationManifest("artifacts/p0.4c/evaluation/key.json", tuple(reversed(expectations())))
        self.assertEqual(left.normalized_json(), right.normalized_json())
        left_policy = ManifestActionPolicy(("b", "a"), ("#b", "#a"))
        right_policy = ManifestActionPolicy(("a", "b"), ("#a", "#b"))
        left_execution = ExecutionManifest(ScenarioId.F01, "run_one", "artifacts/p0.4c/run_one", left_policy, 1, fixture_path="fixture/index.html")
        right_execution = ExecutionManifest(ScenarioId.F01, "run_one", "artifacts/p0.4c/run_one", right_policy, 1, fixture_path="fixture/index.html")
        self.assertEqual(left_execution.normalized_json(), right_execution.normalized_json())


if __name__ == "__main__":
    unittest.main()
