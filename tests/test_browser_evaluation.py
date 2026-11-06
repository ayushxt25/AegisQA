    """Focused orchestration checks for the P0.4C unified browser evaluator."""

    from datetime import datetime, timezone
    import tempfile
    import unittest
    from pathlib import Path

    from failure_intelligence.browser_collector import (
        BrowserCollectionError,
        BrowserCollectionResult,
        F05EvaluationResult,
        F05TrialMeasurement,
        F06EvaluationResult,
    )
    from failure_intelligence.browser_evaluation import (
        REPORT_FILENAME,
        BrowserEvaluationRunner,
        build_evaluation_manifest,
        build_execution_manifests,
        evaluate_outcomes,
        write_evaluation_report,
    )
    from failure_intelligence.classifier import (
        DeterministicClassifier,
        InjectedCondition,
        RepeatedAttempt,
        TrustedInfrastructureContext,
    )
    from failure_intelligence.contracts import (
        EvidenceIdFactory,
        EvidenceSource,
        FailureEvent,
        FailureStage,
        canonical_json,
    )
    from failure_intelligence.manifests import ExecutionManifest, ScenarioId


    _TIME = datetime(2026, 11, 6, tzinfo=timezone.utc)


    def _event(run_id, failure_id, stage, records):
        return FailureEvent(
            failure_id=failure_id, run_id=run_id, occurred_at=_TIME, stage=stage,
            observed_facts={"status": "failed"}, evidence_ids=tuple(item.evidence_id for item in records),
        )


    def _browser_result(manifest: ExecutionManifest, _project_root: Path) -> BrowserCollectionResult:
        factory = EvidenceIdFactory(manifest.run_id, clock=lambda: _TIME)
        if manifest.scenario_id in (ScenarioId.F01, ScenarioId.F02):
            record = factory.create(EvidenceSource.ACTION, "runner", {
                "action_kind": "click", "error": "validation_rejected", "selector": "#increment",
            })
            return BrowserCollectionResult(
                _event(manifest.run_id, "failure_{0}".format(manifest.scenario_id.value.lower()),
                    FailureStage.VALIDATION, (record,)),
                (record,), Path(manifest.artifact_directory), None,
            )
        if manifest.scenario_id is ScenarioId.F03:
            action = factory.create(EvidenceSource.ACTION, "runner", {"action_kind": "click", "outcome": "executed"})
            assertion = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
            records = (action, assertion)
            return BrowserCollectionResult(
                _event(manifest.run_id, "failure_f03", FailureStage.ASSERTION, records), records,
                Path(manifest.artifact_directory), False,
            )
        if manifest.scenario_id is ScenarioId.F04:
            network = factory.create(EvidenceSource.NETWORK, "http_server", {"http_status": 503})
            context = TrustedInfrastructureContext(
                "context_{0}".format(manifest.run_id), manifest.run_id, InjectedCondition.SIMULATED_HTTP_503,
            )
            return BrowserCollectionResult(
                _event(manifest.run_id, "failure_f04", FailureStage.ENVIRONMENT, (network,)),
                (network,), Path(manifest.artifact_directory), None, context,
            )
        raise AssertionError("Unexpected standard collector scenario")


    def _f05_result(manifest: ExecutionManifest, _project_root: Path) -> F05EvaluationResult:
        plan = manifest.f05_repeat_plan
        assert plan is not None
        boundary_factory = EvidenceIdFactory(manifest.run_id, clock=lambda: _TIME)
        control_factory = EvidenceIdFactory(plan.control_run_id, clock=lambda: _TIME)
        boundary_records = tuple(
            boundary_factory.create(
                EvidenceSource.EXECUTION_LOG, "assertion",
                {"status": "assertion_passed" if index % 2 else "assertion_failed"},
            )
            for index in range(10)
        )
        control_records = tuple(
            control_factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
            for _ in range(10)
        )
        boundary_event = _event(manifest.run_id, "failure_f05_boundary", FailureStage.ASSERTION, boundary_records)
        control_event = _event(plan.control_run_id, "failure_f05_control", FailureStage.ASSERTION, control_records)
        boundary_trials = tuple(
            F05TrialMeasurement(
                attempt_id, 25 if index % 2 == 0 else 300, 1, 25 if index % 2 else 100,
                index % 2 == 1, record.evidence_id,
            )
            for index, (attempt_id, record) in enumerate(zip(plan.boundary_attempt_ids, boundary_records))
        )
        control_trials = tuple(
            F05TrialMeasurement(attempt_id, 0, 1, 1, True, record.evidence_id)
            for attempt_id, record in zip(plan.control_attempt_ids, control_records)
        )
        attempts = tuple(
            RepeatedAttempt(trial.attempt_id, plan.equivalence_key, trial.assertion_passed, (trial.assertion_evidence_id,))
            for trial in boundary_trials
        )
        classification = DeterministicClassifier().classify(boundary_event, boundary_records, repeated_attempts=attempts)
        return F05EvaluationResult(
            control_event, boundary_event, control_records, boundary_records, control_trials, boundary_trials,
            attempts, classification,
        )


    def _f06_result(manifest: ExecutionManifest, _project_root: Path) -> F06EvaluationResult:
        factory = EvidenceIdFactory(manifest.run_id, clock=lambda: _TIME)
        record = factory.create(EvidenceSource.ACTION, "runner", {"action_kind": "click", "outcome": "executed"})
        missing_id = "ev_{0}_000002".format(manifest.run_id)
        event = FailureEvent(
            failure_id="failure_f06", run_id=manifest.run_id, occurred_at=_TIME, stage=FailureStage.ASSERTION,
            observed_facts={"status": "failed"}, evidence_ids=(record.evidence_id, missing_id),
        )
        classification = DeterministicClassifier().classify(event, (record,))
        return F06EvaluationResult(event, (record,), missing_id, Path(manifest.artifact_directory), classification)


    class BrowserEvaluationRunnerTest(unittest.TestCase):
        def runner(self, root):
            return BrowserEvaluationRunner(root, browser_collector=_browser_result,
                                        f05_collector=_f05_result, f06_collector=_f06_result)

        def test_execution_manifests_are_isolated_from_ground_truth(self):
            manifests = build_execution_manifests("evaluation_isolation", Path.cwd())
            self.assertEqual(set(manifests), set(ScenarioId))
            self.assertTrue(all("expected_" not in manifest.normalized_json() for manifest in manifests.values()))
            self.assertNotEqual(manifests[ScenarioId.F05].run_id,
                                manifests[ScenarioId.F05].f05_repeat_plan.control_run_id)

        def test_complete_six_scenario_execution_uses_actual_collector_outputs(self):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                report = self.runner(root).run("evaluation_complete")
                report_path = root / "artifacts" / "p0.4c" / "evaluation_complete" / REPORT_FILENAME
                manifest_path = root / "artifacts" / "p0.4c" / "evaluation_complete" / "evaluation-manifest.json"
                self.assertTrue(report_path.is_file())
                self.assertTrue(manifest_path.is_file())
                self.assertEqual(report["metrics"]["completed_scenario_count"], 6)
                self.assertEqual(report["metrics"]["classification_accuracy"], 1.0)
                self.assertEqual(report["metrics"]["confidence_level_agreement"], 1.0)
                self.assertEqual(report["metrics"]["false_application_defect_count"], 0)
                self.assertEqual(report["metrics"]["insufficient_evidence_handling"],
                                {"correct": 1, "expected": 1, "rate": 1.0})
                f05 = next(item for item in report["results"] if item["scenario_id"] == "F05")
                self.assertEqual((f05["details"]["control"]["passed"], f05["details"]["boundary"]["failed"]),
                                (10, 5))

        def test_partial_execution_is_not_counted_as_correct(self):
            def unavailable_f02(manifest, root):
                if manifest.scenario_id is ScenarioId.F02:
                    raise BrowserCollectionError("safe test failure")
                return _browser_result(manifest, root)

            with tempfile.TemporaryDirectory() as directory:
                report = BrowserEvaluationRunner(
                    Path(directory), browser_collector=unavailable_f02,
                    f05_collector=_f05_result, f06_collector=_f06_result,
                ).run("evaluation_partial")
                f02 = next(item for item in report["results"] if item["scenario_id"] == "F02")
                self.assertEqual((f02["status"], f02["error_category"], f02["category_match"]),
                                ("incomplete", "collection_unavailable", False))
                self.assertEqual(report["metrics"]["completed_scenario_count"], 5)
                self.assertEqual(report["metrics"]["classification_accuracy"], 5 / 6)

        def test_report_order_is_deterministic_and_artifact_paths_are_restricted(self):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifests = build_execution_manifests("evaluation_order", root)
                outcomes = [self.runner(root)._collect(manifests[scenario_id]) for scenario_id in ScenarioId]
                answer_key = build_evaluation_manifest("evaluation_order")
                forward = evaluate_outcomes("evaluation_order", outcomes, answer_key)
                reverse = evaluate_outcomes("evaluation_order", tuple(reversed(outcomes)), answer_key)
                self.assertEqual(canonical_json(forward), canonical_json(reverse))
                with self.assertRaises(ValueError):
                    write_evaluation_report({"evaluation_run_id": "../outside"}, root)


    if __name__ == "__main__":
        unittest.main()
