"""Attempt-scoped adapter for the Phase 0 deterministic classifier."""
from __future__ import annotations

import uuid

from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError

from backend.database import DatabaseManager
from backend.models import ExecutionAttempt, ExecutionClassification, ExecutionEvidence, ExecutionEvent, ExecutionJob
from failure_intelligence.classifier import DeterministicClassifier
from failure_intelligence.contracts import EvidenceRecord, EvidenceSource, FailureEvent, FailureStage


CLASSIFIER_VERSION = "p0.4b.2-deterministic"


class AttemptClassificationService:
    """Rebuild only digest-valid Phase 0 records; child output is never trusted."""

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database
        self._classifier = DeterministicClassifier()

    def classify_failed_attempt(self, project_id, job_id, attempt_id):
        """Classify a terminal evidence snapshot; locks are held only for insert."""
        with self._database.session_scope() as session:
            attempt = session.scalar(select(ExecutionAttempt).where(
                ExecutionAttempt.id == attempt_id, ExecutionAttempt.project_id == project_id,
                ExecutionAttempt.execution_job_id == job_id,
            ))
            job = session.scalar(select(ExecutionJob).where(
                ExecutionJob.id == job_id, ExecutionJob.project_id == project_id,
            ))
            if attempt is None or job is None or attempt.status != "failed" or job.status != "failed":
                return None
            existing = session.scalar(select(ExecutionClassification).where(
                ExecutionClassification.execution_attempt_id == attempt.id,
            ))
            if existing is not None:
                return existing

            rows = list(session.scalars(select(ExecutionEvidence).where(
                ExecutionEvidence.project_id == project_id,
                ExecutionEvidence.execution_job_id == job_id,
                ExecutionEvidence.execution_attempt_id == attempt_id,
            ).order_by(ExecutionEvidence.captured_at, ExecutionEvidence.id)))
            if not rows:
                return None
            records = []
            evidence_ids = []
            for row in rows:
                evidence_ids.append(row.evidence_id)
                try:
                    record = EvidenceRecord(
                        evidence_id=row.evidence_id, run_id=str(attempt.id),
                        source=EvidenceSource(row.source), producing_component=row.producing_component,
                        captured_at=row.captured_at, observed_facts=row.observed_facts,
                        content_digest=row.content_digest,
                    )
                except (TypeError, ValueError):
                    continue
                if row.schema_version == "1.0.0" and record.verify_digest():
                    records.append(record)

            # Referencing invalid/missing rows makes R01 fail closed.  The sentinel is
            # only used when no evidence was committed, and is not persisted as evidence.
            event_ids = tuple(evidence_ids) or ("missing_attempt_evidence",)
            event = FailureEvent(
                failure_id="failure_{0}".format(attempt.id), run_id=str(attempt.id),
                occurred_at=attempt.finished_at or job.updated_at,
                stage=FailureStage.ASSERTION, observed_facts={"status": "assertion_failed"},
                evidence_ids=event_ids,
            )
            result = self._classifier.classify(event, records)
            finding = result.finding
            references = [
                {"evidence_id": record.evidence_id, "content_digest": record.content_digest}
                for record in records if record.evidence_id in set(finding.supporting_evidence_ids + finding.contradictory_evidence_ids)
            ]
            values = dict(
                id=uuid.uuid4(), project_id=project_id, execution_job_id=job_id,
                execution_attempt_id=attempt_id, classifier_version=CLASSIFIER_VERSION,
                category=finding.classification.value, confidence=finding.confidence.value,
                applied_rule_id=result.applied_rule_id, evidence_references=references,
                missing_evidence=list(finding.missing_evidence),
            )

        # Never hold the attempt/job locks while rebuilding evidence or running rules.
        try:
            with self._database.session_scope() as session:
                existing = session.scalar(select(ExecutionClassification).where(
                    ExecutionClassification.execution_attempt_id == attempt_id,
                ))
                if existing is not None:
                    return existing
                current = session.scalar(select(ExecutionAttempt).where(
                    ExecutionAttempt.id == attempt_id, ExecutionAttempt.project_id == project_id,
                    ExecutionAttempt.execution_job_id == job_id, ExecutionAttempt.status == "failed",
                ).with_for_update())
                if current is None:
                    return None
                classification = ExecutionClassification(**values)
                session.add(classification)
                session.add(ExecutionEvent(
                    id=uuid.uuid4(), project_id=project_id, execution_job_id=job_id,
                    execution_attempt_id=attempt_id, correlation_id=str(uuid.uuid4()),
                    category="execution_classified", event_metadata={"rule": result.applied_rule_id},
                ))
                session.flush()
                return classification
        except IntegrityError:
            # The unique attempt constraint resolves concurrent recovery safely.
            with self._database.session_scope() as session:
                return session.scalar(select(ExecutionClassification).where(
                    ExecutionClassification.execution_attempt_id == attempt_id,
                ))

    def recover_failed_attempts(self, limit: int = 100) -> int:
        """Bounded, deterministic maintenance pass; it never reruns browser work."""
        if not 1 <= limit <= 1000:
            raise ValueError("recovery limit is invalid")
        with self._database.session_scope() as session:
            candidates = session.execute(
                select(ExecutionAttempt.project_id, ExecutionAttempt.execution_job_id, ExecutionAttempt.id)
                .join(ExecutionJob, and_(
                    ExecutionJob.id == ExecutionAttempt.execution_job_id,
                    ExecutionJob.project_id == ExecutionAttempt.project_id,
                ))
                .where(
                    ExecutionAttempt.status == "failed", ExecutionJob.status == "failed",
                    ~exists(select(ExecutionClassification.id).where(
                        ExecutionClassification.execution_attempt_id == ExecutionAttempt.id,
                    )),
                    exists(select(ExecutionEvidence.id).where(
                        ExecutionEvidence.project_id == ExecutionAttempt.project_id,
                        ExecutionEvidence.execution_job_id == ExecutionAttempt.execution_job_id,
                        ExecutionEvidence.execution_attempt_id == ExecutionAttempt.id,
                    )),
                )
                .order_by(ExecutionAttempt.finished_at, ExecutionAttempt.id).limit(limit)
            ).all()
        recovered = 0
        for project_id, job_id, attempt_id in candidates:
            try:
                if self.classify_failed_attempt(project_id, job_id, attempt_id) is not None:
                    recovered += 1
            except Exception:
                # Leave the attempt eligible for a future bounded sweep.  Do not
                # log raw evidence or turn a classification outage into a job change.
                continue
        return recovered
