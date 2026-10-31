"""Deterministic, evidence-only P0.4B.2 failure classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, Optional, Sequence, Tuple

from .contracts import (
    ConfidenceLevel,
    EvidenceRecord,
    EvidenceSource,
    FailureCategory,
    FailureEvent,
    InvestigationFinding,
    ReproducibilityStatus,
)


@dataclass(frozen=True)
class TrustedAssertionContract:
    """Caller-convention trusted expected assertion outcome; never taken from evidence text."""

    contract_id: str
    run_id: str
    expected_assertion_passed: bool

    def __post_init__(self) -> None:
        if not self.contract_id or not self.run_id:
            raise ValueError("Trusted assertion contract IDs must be non-empty.")
        if not isinstance(self.expected_assertion_passed, bool):
            raise TypeError("expected_assertion_passed must be boolean.")


@dataclass(frozen=True)
class RepeatedAttempt:
    """Evaluator-supplied equivalence metadata for the P0.4C repeat protocol."""

    attempt_id: str
    equivalence_key: str
    assertion_passed: bool
    evidence_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.attempt_id or not self.equivalence_key or not self.evidence_ids:
            raise ValueError("Repeated attempts require IDs, an equivalence key, and evidence.")
        if not isinstance(self.assertion_passed, bool):
            raise TypeError("assertion_passed must be boolean.")


class InjectedCondition(str, Enum):
    SIMULATED_HTTP_503 = "simulated_http_503"


@dataclass(frozen=True)
class TrustedInfrastructureContext:
    """Caller-convention trusted context for a controlled injected dependency condition."""

    context_id: str
    run_id: str
    injected_condition: InjectedCondition

    def __post_init__(self) -> None:
        if not self.context_id or not self.run_id:
            raise ValueError("Trusted infrastructure context IDs must be non-empty.")
        if not isinstance(self.injected_condition, InjectedCondition):
            raise TypeError("injected_condition must be an InjectedCondition.")


@dataclass(frozen=True)
class ClassificationResult:
    finding: InvestigationFinding
    applied_rule_id: str


class DeterministicClassifier:
    """Rule order is R01, R02, R03, R04, R05, then R06."""

    def classify(
        self,
        event: FailureEvent,
        evidence: Iterable[EvidenceRecord],
        assertion_contract: Optional[TrustedAssertionContract] = None,
        repeated_attempts: Sequence[RepeatedAttempt] = (),
        infrastructure_context: Optional[TrustedInfrastructureContext] = None,
    ) -> ClassificationResult:
        records = tuple(sorted(evidence, key=lambda item: item.evidence_id))
        valid, missing = self._valid_event_evidence(event, records)
        duplicate_errors = self._duplicate_evidence_errors(records)
        if missing or duplicate_errors:
            return self._finding(
                event, "R01", FailureCategory.INSUFFICIENT_EVIDENCE,
                ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE,
                valid, (), tuple(sorted(set(missing + duplicate_errors))),
                "Required evidence is missing, invalid, ambiguous, or belongs to another run.",
                ReproducibilityStatus.INCONCLUSIVE,
            )

        validation = self._select(valid, EvidenceSource.ACTION, "runner", "error", "validation_rejected")
        if validation:
            contradictory = self._select_any(valid, EvidenceSource.ACTION, "runner", "outcome", "executed")
            if contradictory:
                return self._finding(
                    event, "R01", FailureCategory.INSUFFICIENT_EVIDENCE,
                    ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE, validation, contradictory,
                    ("action-level correlation resolving rejected and executed evidence",),
                    "Validation rejection conflicts with an apparently executed action.",
                    ReproducibilityStatus.INCONCLUSIVE,
                )
            return self._finding(
                event, "R02", FailureCategory.AUTOMATION_FAILURE, ConfidenceLevel.VERIFIED,
                validation, contradictory, (), "A runner validation rejection prevented browser execution.",
                ReproducibilityStatus.NOT_ATTEMPTED,
            )

        service_failure = tuple(
            record for record in valid
            if record.source is EvidenceSource.NETWORK
            and record.producing_component == "http_server"
            and isinstance(record.observed_facts.get("http_status"), int)
            and 500 <= record.observed_facts["http_status"] <= 599
        )
        if service_failure:
            trusted_injection = (
                infrastructure_context is not None
                and infrastructure_context.run_id == event.run_id
                and infrastructure_context.injected_condition is InjectedCondition.SIMULATED_HTTP_503
                and any(record.observed_facts["http_status"] == 503 for record in service_failure)
            )
            return self._finding(
                event, "R03", FailureCategory.INFRASTRUCTURE_FAILURE,
                ConfidenceLevel.VERIFIED if trusted_injection else ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE,
                service_failure, (),
                () if trusted_injection else ("trusted injected-condition context for infrastructure cause",),
                "Verified injected 503 condition." if trusted_injection else "Observed HTTP 5xx establishes service failure, not root cause.",
                ReproducibilityStatus.NOT_ATTEMPTED,
            )

        application = self._application_evidence(event, valid, assertion_contract)
        if application is not None:
            supporting, contradictory, missing = application
            if contradictory:
                return self._finding(
                    event, "R01", FailureCategory.INSUFFICIENT_EVIDENCE,
                    ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE, supporting, contradictory,
                    ("assertion-level correlation resolving passed and failed evidence",),
                    "Assertion-passed and assertion-failed evidence conflict.",
                    ReproducibilityStatus.INCONCLUSIVE,
                )
            if not missing:
                return self._finding(
                    event, "R04", FailureCategory.APPLICATION_DEFECT, ConfidenceLevel.VERIFIED,
                    supporting, contradictory, (),
                    "Trusted expected behavior contradicts a verified completed action outcome.",
                    ReproducibilityStatus.NOT_ATTEMPTED,
                )

        attempts_missing = self._attempt_evidence_errors(event, valid, repeated_attempts)
        if attempts_missing:
            return self._finding(
                event, "R01", FailureCategory.INSUFFICIENT_EVIDENCE,
                ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE, valid, (), tuple(attempts_missing),
                "Repeated-attempt evidence is missing, invalid, or belongs to another run.",
                ReproducibilityStatus.INCONCLUSIVE,
            )
        if self._is_intermittent(repeated_attempts):
            supporting = self._attempt_records(valid, repeated_attempts)
            return self._finding(
                event, "R05", FailureCategory.FLAKY_OR_INTERMITTENT,
                ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE, supporting, (), (),
                "Equivalent repeated attempts have inconsistent recorded assertion outcomes.",
                ReproducibilityStatus.REPRODUCED,
            )

        missing = ["trusted assertion contract and complete action/assertion evidence"]
        if any(record.observed_facts.get("error") == "timeout" for record in valid):
            missing.append("verified service failure evidence; a generic timeout is insufficient")
        if len(repeated_attempts) < 2:
            missing.append("equivalent repeated attempts with inconsistent outcomes")
        return self._finding(
            event, "R06", FailureCategory.INSUFFICIENT_EVIDENCE,
            ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE, valid, (), tuple(missing),
            "No deterministic classification rule has complete trusted evidence.",
            ReproducibilityStatus.NOT_ATTEMPTED,
        )

    def _valid_event_evidence(
        self, event: FailureEvent, records: Sequence[EvidenceRecord]
    ) -> Tuple[Tuple[EvidenceRecord, ...], Tuple[str, ...]]:
        indexed: Dict[str, Tuple[EvidenceRecord, ...]] = {}
        for record in records:
            indexed[record.evidence_id] = indexed.get(record.evidence_id, ()) + (record,)
        valid = []
        missing = []
        for evidence_id in event.evidence_ids:
            candidates = indexed.get(evidence_id, ())
            if not candidates:
                missing.append("missing evidence: {0}".format(evidence_id))
                continue
            if len({candidate.normalized_json() for candidate in candidates}) != 1:
                missing.append("ambiguous duplicate evidence ID: {0}".format(evidence_id))
                continue
            record = candidates[0]
            if record.run_id != event.run_id:
                missing.append("mismatched run evidence: {0}".format(evidence_id))
            elif not record.verify_digest():
                missing.append("invalid digest evidence: {0}".format(evidence_id))
            else:
                valid.append(record)
        return tuple(valid), tuple(missing)

    @staticmethod
    def _duplicate_evidence_errors(records: Sequence[EvidenceRecord]) -> Tuple[str, ...]:
        """Reject conflicting IDs instead of allowing index construction to overwrite them."""
        by_id: Dict[str, Tuple[EvidenceRecord, ...]] = {}
        for record in records:
            by_id[record.evidence_id] = by_id.get(record.evidence_id, ()) + (record,)
        return tuple(
            "ambiguous duplicate evidence ID: {0}".format(evidence_id)
            for evidence_id, candidates in sorted(by_id.items())
            if len({candidate.normalized_json() for candidate in candidates}) > 1
        )

    @staticmethod
    def _select(
        records: Sequence[EvidenceRecord], source: EvidenceSource, component: str,
        field: str, value: object,
    ) -> Tuple[EvidenceRecord, ...]:
        return tuple(record for record in records if record.source is source
                     and record.producing_component == component
                     and record.observed_facts.get(field) == value)

    @staticmethod
    def _select_any(
        records: Sequence[EvidenceRecord], source: EvidenceSource, component: str,
        field: str, value: object,
    ) -> Tuple[EvidenceRecord, ...]:
        return DeterministicClassifier._select(records, source, component, field, value)

    def _application_evidence(
        self, event: FailureEvent, records: Sequence[EvidenceRecord],
        contract: Optional[TrustedAssertionContract],
    ) -> Optional[Tuple[Tuple[EvidenceRecord, ...], Tuple[EvidenceRecord, ...], Tuple[str, ...]]]:
        if contract is None or contract.run_id != event.run_id or not contract.expected_assertion_passed:
            return None
        action = self._select(records, EvidenceSource.ACTION, "runner", "outcome", "executed")
        assertion = self._select(records, EvidenceSource.EXECUTION_LOG, "assertion", "status", "assertion_failed")
        missing = []
        if not action:
            missing.append("verified completed action evidence")
        if not assertion:
            missing.append("verified contradictory assertion evidence")
        contradictory = self._select(records, EvidenceSource.EXECUTION_LOG, "assertion", "status", "assertion_passed")
        return action + assertion, contradictory, tuple(missing)

    def _attempt_evidence_errors(
        self, event: FailureEvent, records: Sequence[EvidenceRecord],
        attempts: Sequence[RepeatedAttempt],
    ) -> Tuple[str, ...]:
        if not attempts:
            return ()
        event_evidence_ids = set(event.evidence_ids)
        indexed = {record.evidence_id: record for record in records}
        errors = []
        attempt_ids = [attempt.attempt_id for attempt in attempts]
        if len(set(attempt_ids)) != len(attempt_ids):
            errors.append("duplicate repeated-attempt ID")
        for attempt in attempts:
            statuses = []
            for evidence_id in attempt.evidence_ids:
                if evidence_id not in event_evidence_ids:
                    errors.append("repeated-attempt evidence outside event: {0}".format(evidence_id))
                    continue
                record = indexed.get(evidence_id)
                if (record is None or record.run_id != event.run_id or not record.verify_digest()
                        or record.source is not EvidenceSource.EXECUTION_LOG
                        or record.producing_component != "assertion"):
                    errors.append("invalid repeated-attempt evidence: {0}".format(evidence_id))
                    continue
                status = record.observed_facts.get("status")
                if status not in ("assertion_passed", "assertion_failed"):
                    errors.append("ambiguous repeated-attempt assertion evidence: {0}".format(evidence_id))
                    continue
                statuses.append(status)
            if len(statuses) != 1:
                errors.append("conflicting repeated-attempt assertion evidence: {0}".format(attempt.attempt_id))
            elif attempt.assertion_passed != (statuses[0] == "assertion_passed"):
                errors.append("repeated-attempt outcome mismatch: {0}".format(attempt.attempt_id))
        return tuple(sorted(set(errors)))

    @staticmethod
    def _is_intermittent(attempts: Sequence[RepeatedAttempt]) -> bool:
        return (len(attempts) >= 2 and len({attempt.equivalence_key for attempt in attempts}) == 1
                and len({attempt.assertion_passed for attempt in attempts}) == 2)

    @staticmethod
    def _attempt_records(records: Sequence[EvidenceRecord], attempts: Sequence[RepeatedAttempt]) -> Tuple[EvidenceRecord, ...]:
        ids = {evidence_id for attempt in attempts for evidence_id in attempt.evidence_ids}
        return tuple(record for record in records if record.evidence_id in ids)

    @staticmethod
    def _finding(
        event: FailureEvent, rule: str, category: FailureCategory, confidence: ConfidenceLevel,
        supporting: Sequence[EvidenceRecord], contradictory: Sequence[EvidenceRecord], missing: Tuple[str, ...],
        uncertainty: str, reproducibility: ReproducibilityStatus,
    ) -> ClassificationResult:
        supporting_ids = tuple(record.evidence_id for record in supporting)
        if confidence is ConfidenceLevel.VERIFIED and not supporting_ids:
            confidence = ConfidenceLevel.EVIDENCE_SUPPORTED_INFERENCE
        finding = InvestigationFinding(
            finding_id="find_{0}_{1}".format(event.failure_id, rule.lower()), classification=category,
            confidence=confidence, observed_facts=("deterministic_rule:{0}".format(rule),),
            supporting_evidence_ids=supporting_ids,
            contradictory_evidence_ids=tuple(record.evidence_id for record in contradictory),
            uncertainty=uncertainty, missing_evidence=missing, reproducibility=reproducibility,
        )
        return ClassificationResult(finding=finding, applied_rule_id=rule)
