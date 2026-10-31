"""Deterministic synthetic evaluation for the P0.4B classifier.

Scenario inputs deliberately exclude expected classifications.  The private
answer key is used only after a classifier result has been produced.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .classifier import (
    ClassificationResult,
    DeterministicClassifier,
    InjectedCondition,
    RepeatedAttempt,
    TrustedAssertionContract,
    TrustedInfrastructureContext,
)
from .contracts import (
    ConfidenceLevel,
    EvidenceIdFactory,
    EvidenceRecord,
    EvidenceSource,
    FailureCategory,
    FailureEvent,
    FailureStage,
    canonical_json,
)


_FIXED_TIME = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
_ARTIFACTS_DIRECTORY = Path("artifacts").resolve()


@dataclass(frozen=True)
class SyntheticScenario:
    """Classifier-only scenario data; it intentionally has no expected result."""

    scenario_id: str
    failure_event: FailureEvent
    evidence: Tuple[EvidenceRecord, ...]
    assertion_contract: Optional[TrustedAssertionContract] = None
    repeated_attempts: Tuple[RepeatedAttempt, ...] = ()
    infrastructure_context: Optional[TrustedInfrastructureContext] = None


@dataclass(frozen=True)
class _EvaluationExpectation:
    """Evaluation-only ground truth; never passed to the classifier."""

    category: FailureCategory
    confidence: ConfidenceLevel


_ANSWER_KEY: Mapping[str, _EvaluationExpectation] = {
    "F01": _EvaluationExpectation(FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
    "F02": _EvaluationExpectation(FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED),
    "F03": _EvaluationExpectation(FailureCategory.APPLICATION_DEFECT, ConfidenceLevel.VERIFIED),
    "F04": _EvaluationExpectation(FailureCategory.INFRASTRUCTURE_FAILURE, ConfidenceLevel.VERIFIED),
    "F05": _EvaluationExpectation(FailureCategory.FLAKY_OR_INTERMITTENT, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
    "F06": _EvaluationExpectation(FailureCategory.INSUFFICIENT_EVIDENCE, ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE),
}


def _factory(scenario_id: str) -> EvidenceIdFactory:
    return EvidenceIdFactory(run_id="run_synthetic_{0}".format(scenario_id.lower()), clock=lambda: _FIXED_TIME)


def _event(scenario_id: str, run_id: str, stage: FailureStage, evidence_ids: Sequence[str]) -> FailureEvent:
    return FailureEvent(
        failure_id="failure_{0}".format(scenario_id.lower()), run_id=run_id,
        occurred_at=_FIXED_TIME, stage=stage, observed_facts={"status": "failed"},
        evidence_ids=tuple(evidence_ids),
    )


def build_scenarios() -> Tuple[SyntheticScenario, ...]:
    """Build F01-F06 execution inputs without accessing evaluation ground truth."""
    scenarios = []

    factory = _factory("F01")
    rejection = factory.create(EvidenceSource.ACTION, "runner", {
        "action_kind": "click", "error": "validation_rejected", "selector": "#missing",
    })
    scenarios.append(SyntheticScenario("F01", _event("F01", factory.run_id, FailureStage.VALIDATION, (rejection.evidence_id,)), (rejection,)))

    factory = _factory("F02")
    disabled = factory.create(EvidenceSource.ACTION, "runner", {
        "action_kind": "click", "available": True, "enabled": False,
        "error": "validation_rejected", "selector": "#increment", "visible": True,
    })
    scenarios.append(SyntheticScenario("F02", _event("F02", factory.run_id, FailureStage.VALIDATION, (disabled.evidence_id,)), (disabled,)))

    factory = _factory("F03")
    action = factory.create(EvidenceSource.ACTION, "runner", {"action_kind": "click", "outcome": "executed"})
    assertion = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
    scenarios.append(SyntheticScenario(
        "F03", _event("F03", factory.run_id, FailureStage.ASSERTION, (action.evidence_id, assertion.evidence_id)),
        (action, assertion), TrustedAssertionContract("contract_f03", factory.run_id, True),
    ))

    factory = _factory("F04")
    unavailable = factory.create(EvidenceSource.NETWORK, "http_server", {"http_status": 503})
    scenarios.append(SyntheticScenario(
        "F04", _event("F04", factory.run_id, FailureStage.ENVIRONMENT, (unavailable.evidence_id,)), (unavailable,),
        infrastructure_context=TrustedInfrastructureContext(
            "context_f04", factory.run_id, InjectedCondition.SIMULATED_HTTP_503,
        ),
    ))

    factory = _factory("F05")
    failed = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
    passed = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
    scenarios.append(SyntheticScenario(
        "F05", _event("F05", factory.run_id, FailureStage.ASSERTION, (failed.evidence_id, passed.evidence_id)),
        (failed, passed), repeated_attempts=(
            RepeatedAttempt("attempt_f05_1", "declared_equivalent_f05", False, (failed.evidence_id,)),
            RepeatedAttempt("attempt_f05_2", "declared_equivalent_f05", True, (passed.evidence_id,)),
        ),
    ))

    factory = _factory("F06")
    missing_id = "ev_{0}_000001".format(factory.run_id)
    scenarios.append(SyntheticScenario(
        "F06", _event("F06", factory.run_id, FailureStage.ASSERTION, (missing_id,)), (),
    ))
    return tuple(scenarios)


def build_adversarial_scenarios() -> Tuple[SyntheticScenario, ...]:
    """Build malformed evidence cases used to prove conservative classifier behavior."""
    scenarios = []

    factory = _factory("ADV01")
    original = factory.create(EvidenceSource.ACTION, "runner", {"error": "validation_rejected"})
    corrupted = EvidenceRecord(
        original.evidence_id, original.run_id, original.source, original.producing_component,
        original.captured_at, original.observed_facts, content_digest="0" * 64,
    )
    scenarios.append(SyntheticScenario(
        "ADV01_CORRUPTED_DIGEST", _event("ADV01", factory.run_id, FailureStage.VALIDATION, (corrupted.evidence_id,)),
        (corrupted,),
    ))

    factory = _factory("ADV02")
    scenarios.append(SyntheticScenario(
        "ADV02_MISSING_REFERENCE", _event("ADV02", factory.run_id, FailureStage.EXECUTION,
        ("ev_{0}_000001".format(factory.run_id),)), (),
    ))

    factory = _factory("ADV03")
    rejected = factory.create(EvidenceSource.ACTION, "runner", {"error": "validation_rejected"})
    executed = factory.create(EvidenceSource.ACTION, "runner", {"outcome": "executed"})
    scenarios.append(SyntheticScenario(
        "ADV03_CONTRADICTORY_ACTIONS", _event("ADV03", factory.run_id, FailureStage.EXECUTION,
        (rejected.evidence_id, executed.evidence_id)), (rejected, executed),
    ))

    factory = _factory("ADV04")
    action = factory.create(EvidenceSource.ACTION, "runner", {"outcome": "executed"})
    failed = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_failed"})
    passed = factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {"status": "assertion_passed"})
    scenarios.append(SyntheticScenario(
        "ADV04_CONTRADICTORY_ASSERTIONS", _event("ADV04", factory.run_id, FailureStage.ASSERTION,
        (action.evidence_id, failed.evidence_id, passed.evidence_id)), (action, failed, passed),
        TrustedAssertionContract("contract_adv04", factory.run_id, True),
    ))

    factory = _factory("ADV05")
    foreign = EvidenceIdFactory(run_id="run_other", clock=lambda: _FIXED_TIME).create(
        EvidenceSource.ACTION, "runner", {"error": "validation_rejected"},
    )
    scenarios.append(SyntheticScenario(
        "ADV05_CROSS_RUN", _event("ADV05", factory.run_id, FailureStage.VALIDATION, (foreign.evidence_id,)), (foreign,),
    ))

    factory = _factory("ADV06")
    first = EvidenceRecord("ev_duplicate", factory.run_id, EvidenceSource.ACTION, "runner", _FIXED_TIME,
        {"error": "validation_rejected"}).finalized()
    second = EvidenceRecord("ev_duplicate", factory.run_id, EvidenceSource.ACTION, "runner", _FIXED_TIME,
        {"outcome": "executed"}).finalized()
    scenarios.append(SyntheticScenario(
        "ADV06_DUPLICATE_ID", _event("ADV06", factory.run_id, FailureStage.EXECUTION, (first.evidence_id,)),
        (first, second),
    ))
    return tuple(scenarios)


def classify_scenario(
    scenario: SyntheticScenario, classifier: Optional[DeterministicClassifier] = None,
) -> ClassificationResult:
    """Classify execution inputs only; evaluation labels are not an argument."""
    active_classifier = classifier or DeterministicClassifier()
    return active_classifier.classify(
        scenario.failure_event, scenario.evidence, scenario.assertion_contract,
        scenario.repeated_attempts, scenario.infrastructure_context,
    )


def evaluate_scenarios(
    scenarios: Optional[Iterable[SyntheticScenario]] = None,
    classifier: Optional[DeterministicClassifier] = None,
) -> Dict[str, object]:
    """Classify all scenarios, then join results to the isolated answer key."""
    scenario_list = tuple(sorted(scenarios or build_scenarios(), key=lambda item: item.scenario_id))
    if tuple(item.scenario_id for item in scenario_list) != tuple(sorted(_ANSWER_KEY)):
        raise ValueError("Evaluation requires exactly the F01-F06 scenario set.")

    results = []
    category_matches = 0
    confidence_matches = 0
    false_application_defects = 0
    expected_insufficient = 0
    correctly_handled_insufficient = 0
    for scenario in scenario_list:
        classified = classify_scenario(scenario, classifier)
        expected = _ANSWER_KEY[scenario.scenario_id]
        finding = classified.finding
        category_match = finding.classification is expected.category
        confidence_match = finding.confidence is expected.confidence
        category_matches += int(category_match)
        confidence_matches += int(confidence_match)
        false_application_defects += int(
            finding.classification is FailureCategory.APPLICATION_DEFECT
            and expected.category is not FailureCategory.APPLICATION_DEFECT
        )
        if expected.category is FailureCategory.INSUFFICIENT_EVIDENCE:
            expected_insufficient += 1
            correctly_handled_insufficient += int(finding.classification is FailureCategory.INSUFFICIENT_EVIDENCE)
        results.append({
            "actual_category": finding.classification.value,
            "actual_confidence": finding.confidence.value,
            "applied_rule_id": classified.applied_rule_id,
            "category_match": category_match,
            "confidence_match": confidence_match,
            "scenario_id": scenario.scenario_id,
        })

    total = len(scenario_list)
    return {
        "evaluation_schema_version": "1.0.0",
        "metrics": {
            "accuracy": category_matches / total,
            "confidence_level_agreement": confidence_matches / total,
            "false_application_defect_count": false_application_defects,
            "insufficient_evidence_handling": {
                "correct": correctly_handled_insufficient,
                "expected": expected_insufficient,
                "rate": correctly_handled_insufficient / expected_insufficient,
            },
            "scenario_count": total,
        },
        "results": results,
    }


def normalized_evaluation_json(scenarios: Optional[Iterable[SyntheticScenario]] = None) -> str:
    return canonical_json(evaluate_scenarios(scenarios))


def write_evaluation_output(output_path: Path) -> Path:
    """Write a deterministic report only below the ignored local artifacts directory."""
    target = output_path.resolve()
    try:
        target.relative_to(_ARTIFACTS_DIRECTORY)
    except ValueError as error:
        raise ValueError("Evaluation output must be under the local artifacts directory.") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalized_evaluation_json(), encoding="utf-8")
    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic P0.4B.3 synthetic evaluation.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/synthetic-evaluation.json"))
    arguments = parser.parse_args(argv)
    output = write_evaluation_output(arguments.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
