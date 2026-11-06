"""Unified local P0.4C browser evaluation orchestration.

Execution manifests and collector calls deliberately contain no expected
classification.  The evaluation-only answer key is joined only after every
available scenario has been classified.
"""

from __future__ import annotations

import argparse
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple

from .browser_collector import (
    PROJECT_ROOT,
    BrowserCollectionError,
    BrowserCollectionResult,
    F05EvaluationResult,
    F06EvaluationResult,
    collect_browser_evidence,
    collect_f05_repeated_trials,
    collect_f06_ambiguous_evidence,
)
from .classifier import (
    ClassificationResult,
    DeterministicClassifier,
    TrustedAssertionContract,
)
from .contracts import ConfidenceLevel, FailureCategory, canonical_json
from .manifests import (
    EvaluationExpectation,
    EvaluationManifest,
    ExecutionManifest,
    F05RepeatPlan,
    ManifestActionPolicy,
    ScenarioId,
)


REPORT_SCHEMA_VERSION = "1.0.0"
REPORT_FILENAME = "browser-evaluation-report.json"
_ARTIFACT_ROOT = Path("artifacts") / "p0.4c"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FIXTURES: Mapping[ScenarioId, str] = {
    ScenarioId.F01: "fixture/index.html",
    ScenarioId.F02: "fixture/p0.4c/disabled-button.html",
    ScenarioId.F03: "fixture/p0.4c/incorrect-result.html",
    ScenarioId.F05: "fixture/p0.4c/delayed-result.html",
    ScenarioId.F06: "fixture/index.html",
}


@dataclass(frozen=True)
class ScenarioEvaluation:
    """Sanitized scenario outcome.  No browser exception text is retained."""

    scenario_id: ScenarioId
    run_id: str
    status: str
    classification: Optional[ClassificationResult]
    evidence_count: int = 0
    details: Optional[Mapping[str, object]] = None
    error_category: Optional[str] = None

    def to_dict(self) -> dict:
        payload = {
            "classification": None,
            "evidence_count": self.evidence_count,
            "error_category": self.error_category,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id.value,
            "status": self.status,
        }
        if isinstance(self.classification, ClassificationResult):
            payload["classification"] = {
                "applied_rule_id": self.classification.applied_rule_id,
                "finding": self.classification.finding.to_dict(),
            }
        if self.details is not None:
            payload["details"] = dict(self.details)
        return payload


BrowserCollector = Callable[[ExecutionManifest, Path], BrowserCollectionResult]
F05Collector = Callable[[ExecutionManifest, Path], F05EvaluationResult]
F06Collector = Callable[[ExecutionManifest, Path], F06EvaluationResult]


def _run_id(prefix: str) -> str:
    return "{0}_{1}".format(prefix, uuid.uuid4().hex)


def _validate_evaluation_run_id(value: str) -> str:
    """Accept one safe path component only; generated IDs also use this form."""
    if not isinstance(value, str) or not _SAFE_RUN_ID.fullmatch(value):
        raise ValueError("evaluation_run_id must be a non-empty safe identifier without path components.")
    return value


def _attempt_ids(prefix: str) -> Tuple[str, ...]:
    return tuple("{0}_{1:02d}".format(prefix, number) for number in range(1, 11))


def _fixture_url(project_root: Path, fixture_path: str) -> str:
    return (project_root / fixture_path).resolve().as_uri()


def build_execution_manifests(
    evaluation_run_id: str, project_root: Path = PROJECT_ROOT, timeout_ms: int = 1_000,
) -> Dict[ScenarioId, ExecutionManifest]:
    """Create isolated execution-only manifests for every controlled scenario."""
    evaluation_run_id = _validate_evaluation_run_id(evaluation_run_id)
    manifests: Dict[ScenarioId, ExecutionManifest] = {}
    for scenario_id, fixture_path in _FIXTURES.items():
        if scenario_id is ScenarioId.F05:
            continue
        run_id = "{0}_{1}".format(evaluation_run_id, scenario_id.value.lower())
        manifests[scenario_id] = ExecutionManifest(
            scenario_id=scenario_id,
            run_id=run_id,
            artifact_directory="artifacts/p0.4c/{0}".format(run_id),
            action_policy=ManifestActionPolicy(
                allowed_urls=(_fixture_url(project_root, fixture_path),),
                allowed_click_targets=("#increment",),
                allowed_inspect_targets=("#result",),
            ),
            timeout_ms=timeout_ms,
            fixture_path=fixture_path,
        )

    f04_run_id = "{0}_f04".format(evaluation_run_id)
    manifests[ScenarioId.F04] = ExecutionManifest(
        scenario_id=ScenarioId.F04,
        run_id=f04_run_id,
        artifact_directory="artifacts/p0.4c/{0}".format(f04_run_id),
        action_policy=ManifestActionPolicy(allowed_urls=(), allowed_click_targets=()),
        timeout_ms=timeout_ms,
        server_route="/service-unavailable",
    )

    boundary_run_id = "{0}_f05_boundary".format(evaluation_run_id)
    control_run_id = "{0}_f05_control".format(evaluation_run_id)
    fixture_path = _FIXTURES[ScenarioId.F05]
    manifests[ScenarioId.F05] = ExecutionManifest(
        scenario_id=ScenarioId.F05,
        run_id=boundary_run_id,
        artifact_directory="artifacts/p0.4c/{0}".format(boundary_run_id),
        action_policy=ManifestActionPolicy(
            allowed_urls=(_fixture_url(project_root, fixture_path),),
            allowed_click_targets=("#increment",),
            allowed_inspect_targets=("#result",),
        ),
        timeout_ms=timeout_ms,
        fixture_path=fixture_path,
        f05_repeat_plan=F05RepeatPlan(
            boundary_run_id=boundary_run_id,
            control_run_id=control_run_id,
            boundary_attempt_ids=_attempt_ids("{0}_boundary".format(evaluation_run_id)),
            control_attempt_ids=_attempt_ids("{0}_control".format(evaluation_run_id)),
            equivalence_key="p0_4c_f05_boundary_profile_v1",
        ),
    )
    return manifests


def build_evaluation_manifest(evaluation_run_id: str) -> EvaluationManifest:
    """Return the answer key for the reporting stage only, never for execution."""
    evaluation_run_id = _validate_evaluation_run_id(evaluation_run_id)
    expectations = (
        EvaluationExpectation(ScenarioId.F01, FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F02, FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F03, FailureCategory.APPLICATION_DEFECT, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F04, FailureCategory.INFRASTRUCTURE_FAILURE, ConfidenceLevel.VERIFIED),
        EvaluationExpectation(ScenarioId.F05, FailureCategory.FLAKY_OR_INTERMITTENT,
                              ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
        EvaluationExpectation(ScenarioId.F06, FailureCategory.INSUFFICIENT_EVIDENCE,
                              ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
    )
    return EvaluationManifest(
        manifest_path="artifacts/p0.4c/{0}/evaluation-manifest.json".format(evaluation_run_id),
        expectations=expectations,
    )


def _f05_details(result: F05EvaluationResult) -> dict:
    serialized = result.to_dict()
    return {
        "boundary": serialized["boundary"]["metrics"],
        "control": serialized["control"]["metrics"],
    }


class BrowserEvaluationRunner:
    """Run the existing collectors, then evaluate their actual classifications."""

    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        classifier: Optional[DeterministicClassifier] = None,
        browser_collector: BrowserCollector = collect_browser_evidence,
        f05_collector: F05Collector = collect_f05_repeated_trials,
        f06_collector: F06Collector = collect_f06_ambiguous_evidence,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.classifier = classifier or DeterministicClassifier()
        self.browser_collector = browser_collector
        self.f05_collector = f05_collector
        self.f06_collector = f06_collector

    def _collect(self, manifest: ExecutionManifest) -> ScenarioEvaluation:
        """Collect and classify without access to the evaluation answer key."""
        try:
            if manifest.scenario_id is ScenarioId.F05:
                result = self.f05_collector(manifest, self.project_root)
                return ScenarioEvaluation(
                    manifest.scenario_id, manifest.run_id, "completed", result.classification,
                    len(result.boundary_evidence), _f05_details(result),
                )
            if manifest.scenario_id is ScenarioId.F06:
                result = self.f06_collector(manifest, self.project_root)
                return ScenarioEvaluation(
                    manifest.scenario_id, manifest.run_id, "completed", result.classification,
                    len(result.evidence), {"missing_assertion_evidence_id": result.missing_assertion_evidence_id},
                )

            result = self.browser_collector(manifest, self.project_root)
            assertion_contract = None
            if manifest.scenario_id is ScenarioId.F03:
                assertion_contract = TrustedAssertionContract(
                    "contract_{0}".format(manifest.run_id), manifest.run_id, True,
                )
            classification = self.classifier.classify(
                result.failure_event, result.evidence,
                assertion_contract=assertion_contract,
                infrastructure_context=result.infrastructure_context,
            )
            return ScenarioEvaluation(
                manifest.scenario_id, manifest.run_id, "completed", classification, len(result.evidence),
            )
        except BrowserCollectionError:
            return ScenarioEvaluation(manifest.scenario_id, manifest.run_id, "incomplete", None,
                                      error_category="collection_unavailable")
        except Exception:
            # This boundary never serializes exception text, which may contain sensitive page data.
            return ScenarioEvaluation(manifest.scenario_id, manifest.run_id, "incomplete", None,
                                      error_category="unexpected_collection_failure")

    def run(self, evaluation_run_id: Optional[str] = None) -> dict:
        """Execute F01-F06 and write the evaluation-only report under artifacts/p0.4c/."""
        active_run_id = _validate_evaluation_run_id(evaluation_run_id or _run_id("evaluation"))
        evaluation_directory = self.project_root / _ARTIFACT_ROOT / active_run_id
        if (evaluation_directory / REPORT_FILENAME).exists() or (evaluation_directory / "evaluation-manifest.json").exists():
            raise FileExistsError("Evaluation report or ground-truth manifest already exists for this run ID.")
        manifests = build_execution_manifests(active_run_id, self.project_root)
        outcomes = tuple(self._collect(manifests[scenario_id]) for scenario_id in ScenarioId)
        # Deliberately constructed only after all classifier calls above have completed.
        answer_key = build_evaluation_manifest(active_run_id)
        report = evaluate_outcomes(active_run_id, outcomes, answer_key)
        write_evaluation_manifest(answer_key, self.project_root)
        write_evaluation_report(report, self.project_root)
        return report


def evaluate_outcomes(
    evaluation_run_id: str,
    outcomes: Sequence[ScenarioEvaluation],
    answer_key: EvaluationManifest,
) -> dict:
    """Join classifier outputs to answer-key data only after collection/classification."""
    _validate_evaluation_run_id(evaluation_run_id)
    by_scenario = {}
    for outcome in outcomes:
        if not isinstance(outcome, ScenarioEvaluation) or not isinstance(outcome.scenario_id, ScenarioId):
            raise ValueError("Evaluation outcomes must use known ScenarioId values.")
        if outcome.scenario_id in by_scenario:
            raise ValueError("Evaluation outcomes must not contain duplicate scenario IDs.")
        by_scenario[outcome.scenario_id] = outcome
    expected = {item.scenario_id: item for item in answer_key.expectations}
    results = []
    category_matches = confidence_matches = false_application_defects = 0
    insufficient_correct = insufficient_expected = 0

    for scenario_id in ScenarioId:
        outcome = by_scenario.get(scenario_id)
        expectation = expected[scenario_id]
        category_match = confidence_match = False
        actual_category = actual_confidence = applied_rule_id = None
        valid_completed_classification = (
            outcome is not None
            and outcome.status == "completed"
            and isinstance(outcome.classification, ClassificationResult)
        )
        if valid_completed_classification:
            finding = outcome.classification.finding
            actual_category = finding.classification.value
            actual_confidence = finding.confidence.value
            applied_rule_id = outcome.classification.applied_rule_id
            category_match = finding.classification is expectation.expected_category
            confidence_match = finding.confidence is expectation.expected_confidence
            false_application_defects += int(
                finding.classification is FailureCategory.APPLICATION_DEFECT
                and expectation.expected_category is not FailureCategory.APPLICATION_DEFECT
            )
        category_matches += int(category_match)
        confidence_matches += int(confidence_match)
        if expectation.expected_category is FailureCategory.INSUFFICIENT_EVIDENCE:
            insufficient_expected += 1
            insufficient_correct += int(actual_category == FailureCategory.INSUFFICIENT_EVIDENCE.value)
        result = outcome.to_dict() if outcome is not None else {
            "classification": None, "error_category": "missing_scenario_result", "evidence_count": 0,
            "run_id": None, "scenario_id": scenario_id.value, "status": "incomplete",
        }
        result.update({
            "actual_category": actual_category,
            "actual_confidence": actual_confidence,
            "applied_rule_id": applied_rule_id,
            "category_match": category_match,
            "confidence_match": confidence_match,
            "valid_completed_classification": valid_completed_classification,
            "expected_category": expectation.expected_category.value,
            "expected_confidence": expectation.expected_confidence.value,
        })
        results.append(result)

    total = len(ScenarioId)
    completed = sum(
        item.status == "completed" and isinstance(item.classification, ClassificationResult)
        for item in outcomes
    )
    return {
        "evaluation_run_id": evaluation_run_id,
        "evaluation_schema_version": REPORT_SCHEMA_VERSION,
        "ground_truth_manifest": answer_key.to_dict(),
        "metrics": {
            "classification_accuracy": category_matches / total,
            "classification_accuracy_denominator": total,
            "completed_scenario_count": completed,
            "confidence_level_agreement": confidence_matches / total,
            "confidence_level_agreement_denominator": total,
            "false_application_defect_count": false_application_defects,
            "incomplete_scenario_count": total - completed,
            "insufficient_evidence_handling": {
                "correct": insufficient_correct,
                "expected": insufficient_expected,
                "rate": insufficient_correct / insufficient_expected,
            },
            "scenario_count": total,
        },
        "results": results,
    }


def write_evaluation_report(report: Mapping[str, object], project_root: Path = PROJECT_ROOT) -> Path:
    """Write only below the ignored P0.4C artifact root."""
    run_id = report.get("evaluation_run_id")
    _validate_evaluation_run_id(run_id)
    root = (Path(project_root).resolve() / _ARTIFACT_ROOT).resolve()
    destination = (root / run_id / REPORT_FILENAME).resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise ValueError("Evaluation report must remain under artifacts/p0.4c/.") from error
    if destination.exists():
        raise FileExistsError("Evaluation report already exists for this run ID.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(canonical_json(dict(report)), encoding="utf-8")
    return destination


def write_evaluation_manifest(manifest: EvaluationManifest, project_root: Path = PROJECT_ROOT) -> Path:
    """Persist the answer key only after execution/classification has completed."""
    root = (Path(project_root).resolve() / _ARTIFACT_ROOT).resolve()
    destination = (Path(project_root).resolve() / manifest.manifest_path).resolve()
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise ValueError("Evaluation manifest must remain under artifacts/p0.4c/.") from error
    if destination.exists():
        raise FileExistsError("Evaluation ground-truth manifest already exists for this run ID.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(manifest.normalized_json(), encoding="utf-8")
    return destination


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local P0.4C browser evaluation.")
    parser.add_argument("--run-id", help="Optional safe evaluation run identifier.")
    arguments = parser.parse_args(argv)
    report = BrowserEvaluationRunner().run(arguments.run_id)
    print("artifacts/p0.4c/{0}/{1}".format(report["evaluation_run_id"], REPORT_FILENAME))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
