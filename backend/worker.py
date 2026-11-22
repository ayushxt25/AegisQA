"""Lease-fenced PostgreSQL worker; browser execution is intentionally absent."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Integer, and_, bindparam, func, or_, select, text, update

from backend.database import DatabaseManager
from backend.models import BrowserTargetVersion, ExecutionAttempt, ExecutionEvent, ExecutionEvidence, ExecutionJob
from backend.docker_sandbox import DockerContainmentError, DockerExecutionInterrupted, DockerSandbox, FixtureExecutionRequest
from failure_intelligence.contracts import EvidenceIdFactory, EvidenceRecord, EvidenceSource


_ACTIVE_ATTEMPTS = ("claimed", "starting", "running")
_ACTIVE_JOBS = ("claimed", "running")


@dataclass(frozen=True)
class ClaimedAttempt:
    attempt_id: uuid.UUID
    job_id: uuid.UUID
    project_id: uuid.UUID
    lease_token: str


class ExecutionWorker:
    """Claims only queued work and fences every worker-owned write."""

    def __init__(
        self,
        database: DatabaseManager,
        worker_id: str,
        lease_seconds: int = 30,
        logger: Optional[logging.Logger] = None,
        fixture_sandbox: Optional[DockerSandbox] = None,
        fixture_image_id: Optional[str] = None,
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker ID is invalid")
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("lease duration is invalid")
        self._database = database
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._logger = logger or logging.getLogger("aegisqa.worker")
        self._fixture_sandbox = fixture_sandbox
        self._fixture_image_id = fixture_image_id
        if (fixture_sandbox is None) != (fixture_image_id is None):
            raise ValueError("fixture runtime configuration is incomplete")

    def claim_next(self) -> Optional[ClaimedAttempt]:
        """Atomically claim one non-cancelled, non-expired queued attempt."""

        with self._database.session_scope() as session:
            row = session.execute(
                select(ExecutionAttempt, ExecutionJob)
                .join(
                    ExecutionJob,
                    and_(
                        ExecutionJob.id == ExecutionAttempt.execution_job_id,
                        ExecutionJob.project_id == ExecutionAttempt.project_id,
                    ),
                )
                .outerjoin(BrowserTargetVersion, and_(BrowserTargetVersion.id == ExecutionJob.browser_target_version_id, BrowserTargetVersion.project_id == ExecutionJob.project_id))
                .where(
                    ExecutionAttempt.status == "queued",
                    ExecutionJob.status == "queued",
                    ExecutionJob.cancel_requested_at.is_(None),
                    ExecutionJob.deadline_at > func.now(),
                    or_(ExecutionJob.browser_target_version_id.is_(None), BrowserTargetVersion.approval_state == "approved"),
                )
                .order_by(ExecutionAttempt.created_at, ExecutionAttempt.id)
                .with_for_update(of=(ExecutionAttempt, ExecutionJob), skip_locked=True)
                .limit(1)
            ).first()
            if row is None:
                return None
            attempt, job = row
            if job.browser_target_version_id is not None:
                # Do not lock the nullable outer-join side. Lock the concrete
                # version row separately so revocation cannot race this claim.
                target = session.scalar(
                    select(BrowserTargetVersion)
                    .where(
                        BrowserTargetVersion.project_id == job.project_id,
                        BrowserTargetVersion.id == job.browser_target_version_id,
                    )
                    .with_for_update()
                )
                if target is None or target.approval_state != "approved":
                    return None
            token = secrets.token_urlsafe(32)
            expiry = session.scalar(select(_lease_expiry_expression(self._lease_seconds)))
            attempt.status = "claimed"
            attempt.worker_id = self._worker_id
            attempt.lease_token = token
            attempt.lease_expires_at = expiry
            attempt.last_heartbeat_at = func.now()
            job.status = "claimed"
            job.updated_at = func.now()
            self._event(session, job, attempt, "execution_claimed")
            session.flush()
            return ClaimedAttempt(attempt.id, job.id, job.project_id, token)

    def heartbeat(self, claim: ClaimedAttempt) -> bool:
        """Extend a live lease. A false result is an immediate stop signal."""

        with self._database.session_scope() as session:
            result = session.execute(
                update(ExecutionAttempt)
                .where(
                    ExecutionAttempt.id == claim.attempt_id,
                    ExecutionAttempt.project_id == claim.project_id,
                    ExecutionAttempt.execution_job_id == claim.job_id,
                    ExecutionAttempt.lease_token == claim.lease_token,
                    ExecutionAttempt.status.in_(_ACTIVE_ATTEMPTS),
                    ExecutionAttempt.lease_expires_at > func.now(),
                )
                .values(
                    lease_expires_at=_lease_expiry_expression(self._lease_seconds),
                    last_heartbeat_at=func.now(),
                )
            )
            return result.rowcount == 1

    def begin(self, claim: ClaimedAttempt) -> bool:
        """Perform claimed -> starting -> running, each lease-fenced."""

        if not self._transition(claim, "claimed", "starting", None, "execution_starting"):
            return False
        return self._transition(claim, "starting", "running", "running", "execution_running")

    def process_once(self) -> bool:
        """Process a single claim without inventing a successful executor result."""

        claim = self.claim_next()
        if claim is None:
            return False
        if not self.begin(claim):
            return True
        if self.cancel_requested(claim):
            self.finalize_cancelled(claim)
        elif self.deadline_exceeded(claim):
            self.finalize_timed_out(claim)
        elif self._fixture_sandbox is None:
            # Browser execution belongs to a later milestone. This safe terminal
            # outcome prevents an unimplemented executor from holding a lease.
            self.finalize_failed(claim, "executor_unavailable")
        else:
            self._process_fixture(claim)
        return True

    def _process_fixture(self, claim: ClaimedAttempt) -> None:
        request = self._fixture_request(claim)
        if request is None:
            self.finalize_failed(claim, "unsupported_execution_contract")
            return
        last_heartbeat = [0.0]

        def lease_valid() -> bool:
            now = time.monotonic()
            if now - last_heartbeat[0] < min(1.0, self._lease_seconds / 3):
                return True
            last_heartbeat[0] = now
            return self.heartbeat(claim)

        try:
            result = self._fixture_sandbox.run_playwright_fixture(
                self._fixture_image_id, request, lease_valid=lease_valid,
                cancellation_requested=lambda: self.cancel_requested(claim),
            )
        except DockerExecutionInterrupted as error:
            if str(error) == "cancelled":
                self.finalize_cancelled(claim)
            elif str(error) == "timed_out" or self.deadline_exceeded(claim):
                self.finalize_timed_out(claim)
            # Lease loss is intentionally non-authoritative; the sweeper recovers it.
            return
        except DockerContainmentError:
            self.finalize_failed(claim, "fixture_infrastructure_failed")
            return

        if self.cancel_requested(claim):
            self.finalize_cancelled(claim)
        elif self.deadline_exceeded(claim):
            self.finalize_timed_out(claim)
        elif result.payload.get("outcome") == "assertion_passed" and result.payload.get("assertion_verified") is True:
            self.finalize_succeeded(claim, self._evidence(claim, result.payload))
        elif result.payload.get("outcome") == "assertion_failed":
            self.finalize_failed(claim, "fixture_assertion_failed", self._evidence(claim, result.payload))
        else:
            self.finalize_failed(claim, "fixture_execution_failed")

    def _fixture_request(self, claim: ClaimedAttempt) -> Optional[FixtureExecutionRequest]:
        with self._database.session_scope() as session:
            job = session.scalar(select(ExecutionJob).where(
                ExecutionJob.id == claim.job_id, ExecutionJob.project_id == claim.project_id,
            ))
            if job is None:
                return None
            if job.request_schema_version != "1.0.0" or job.browser_target_version_id is None or job.test_definition_version_id is None:
                return None
            orchestration = job.request_snapshot.get("orchestration") if isinstance(job.request_snapshot, dict) else None
            if not isinstance(orchestration, dict) or set(orchestration) != {"mode", "scenario_id"}:
                return None
            if orchestration.get("mode") != "controlled_fixture":
                return None
            try:
                return FixtureExecutionRequest("1.0", orchestration.get("scenario_id"), min(30.0, float(job.request_snapshot.get("deadline_seconds", 0))))
            except (TypeError, ValueError):
                return None

    def _evidence(self, claim: ClaimedAttempt, payload: dict):
        """Parent constructs sanitized records; child timestamps/claims are untrusted."""
        factory = EvidenceIdFactory(run_id=str(claim.attempt_id))
        observation = payload.get("observation", {})
        return (
            factory.create(EvidenceSource.OBSERVATION, "sandbox.playwright", {
                "url": observation.get("url", ""), "result": observation.get("result"),
                "permitted_elements": observation.get("permitted_element_count", 0),
            }),
            factory.create(EvidenceSource.ACTION, "sandbox.playwright", {
                "action_kind": "click", "outcome": "executed", "target": "#increment",
                "duration_ms": payload.get("duration_ms", 0),
            }),
            factory.create(EvidenceSource.EXECUTION_LOG, "assertion", {
                "status": "assertion_passed" if payload.get("assertion_verified") else "assertion_failed",
            }),
        )

    def _classify_failed_attempt(self, claim: ClaimedAttempt) -> None:
        """Classification is advisory and cannot alter a committed execution outcome."""
        try:
            from backend.classification import AttemptClassificationService
            AttemptClassificationService(self._database).classify_failed_attempt(
                claim.project_id, claim.job_id, claim.attempt_id,
            )
        except Exception:
            self._logger.warning("deterministic classification unavailable", extra={"event": "classification_unavailable"})

    def supervise_child(self, claim: ClaimedAttempt, supervisor, packet: dict):
        """Run a fixed child while retaining the lease; no terminal DB write occurs here."""
        return supervisor.run(
            packet,
            lease_valid=lambda: self.heartbeat(claim),
            cancellation_requested=lambda: self.cancel_requested(claim),
        )

    def cancel_requested(self, claim: ClaimedAttempt) -> bool:
        with self._database.session_scope() as session:
            value = session.scalar(
                select(ExecutionJob.cancel_requested_at).where(
                    ExecutionJob.id == claim.job_id,
                    ExecutionJob.project_id == claim.project_id,
                )
            )
            return value is not None

    def deadline_exceeded(self, claim: ClaimedAttempt) -> bool:
        with self._database.session_scope() as session:
            return bool(
                session.scalar(
                    select(ExecutionJob.id).where(
                        ExecutionJob.id == claim.job_id,
                        ExecutionJob.project_id == claim.project_id,
                        ExecutionJob.deadline_at <= func.now(),
                    )
                )
            )

    def finalize_cancelled(self, claim: ClaimedAttempt) -> bool:
        return self._terminal(claim, "cancelled", "cancelled", "cancellation_requested", "execution_cancelled")

    def finalize_failed(self, claim: ClaimedAttempt, reason: str, evidence=()) -> bool:
        if reason not in {"executor_unavailable", "unsupported_execution_contract", "fixture_infrastructure_failed", "fixture_assertion_failed", "fixture_execution_failed"}:
            raise ValueError("terminal reason is invalid")
        finalized = self._terminal(claim, "failed", "failed", reason, "execution_failed", evidence)
        if finalized and evidence:
            self._classify_failed_attempt(claim)
        return finalized

    def finalize_succeeded(self, claim: ClaimedAttempt, evidence=()) -> bool:
        return self._terminal(claim, "succeeded", "succeeded", "assertion_verified", "execution_succeeded", evidence)

    def finalize_timed_out(self, claim: ClaimedAttempt) -> bool:
        return self._terminal(claim, "timed_out", "timed_out", "deadline_exceeded", "execution_timed_out")

    def sweep(self, limit: int = 100) -> int:
        """Finalize lifecycle recovery, then recover missing advisory classifications."""

        if not 1 <= limit <= 1000:
            raise ValueError("sweep limit is invalid")
        changed = 0
        with self._database.session_scope() as session:
            queued = session.execute(
                select(ExecutionAttempt, ExecutionJob)
                .join(
                    ExecutionJob,
                    and_(
                        ExecutionJob.id == ExecutionAttempt.execution_job_id,
                        ExecutionJob.project_id == ExecutionAttempt.project_id,
                    ),
                )
                .where(
                    ExecutionAttempt.status == "queued",
                    ExecutionJob.status == "queued",
                    ExecutionJob.deadline_at <= func.now(),
                )
                .order_by(ExecutionAttempt.created_at, ExecutionAttempt.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for attempt, job in queued:
                attempt.status = "timed_out"
                attempt.finished_at = func.now()
                attempt.terminal_outcome = "deadline_exceeded"
                job.status = "timed_out"
                job.updated_at = func.now()
                self._event(session, job, attempt, "execution_timed_out")
                changed += 1

            remaining = limit - changed
            if remaining > 0:
                revoked = session.execute(
                    select(ExecutionAttempt, ExecutionJob)
                    .join(ExecutionJob, and_(ExecutionJob.id == ExecutionAttempt.execution_job_id, ExecutionJob.project_id == ExecutionAttempt.project_id))
                    .join(BrowserTargetVersion, and_(BrowserTargetVersion.id == ExecutionJob.browser_target_version_id, BrowserTargetVersion.project_id == ExecutionJob.project_id))
                    .where(ExecutionAttempt.status == "queued", ExecutionJob.status == "queued", BrowserTargetVersion.approval_state != "approved")
                    .with_for_update(skip_locked=True).limit(remaining)
                ).all()
                for attempt, job in revoked:
                    attempt.status = "failed"; attempt.finished_at = func.now(); attempt.terminal_outcome = "target_not_approved"
                    job.status = "failed"; job.updated_at = func.now()
                    self._event(session, job, attempt, "execution_contract_unavailable")
                    changed += 1

            remaining = limit - changed
            if remaining > 0:
                active_deadlines = session.execute(
                    select(ExecutionAttempt, ExecutionJob)
                    .join(
                        ExecutionJob,
                        and_(
                            ExecutionJob.id == ExecutionAttempt.execution_job_id,
                            ExecutionJob.project_id == ExecutionAttempt.project_id,
                        ),
                    )
                    .where(
                        ExecutionAttempt.status.in_(_ACTIVE_ATTEMPTS),
                        ExecutionJob.status.in_(_ACTIVE_JOBS),
                        ExecutionJob.deadline_at <= func.now(),
                    )
                    .order_by(ExecutionJob.deadline_at, ExecutionAttempt.id)
                    .with_for_update(skip_locked=True)
                    .limit(remaining)
                ).all()
                for attempt, job in active_deadlines:
                    attempt.status = "timed_out"
                    attempt.finished_at = func.now()
                    attempt.terminal_outcome = "deadline_exceeded"
                    job.status = "timed_out"
                    job.updated_at = func.now()
                    self._event(session, job, attempt, "execution_timed_out")
                    changed += 1

            remaining = limit - changed
            if remaining > 0:
                abandoned = session.execute(
                    select(ExecutionAttempt, ExecutionJob)
                    .join(
                        ExecutionJob,
                        and_(
                            ExecutionJob.id == ExecutionAttempt.execution_job_id,
                            ExecutionJob.project_id == ExecutionAttempt.project_id,
                        ),
                    )
                    .where(
                        ExecutionAttempt.status.in_(_ACTIVE_ATTEMPTS),
                        ExecutionJob.status.in_(_ACTIVE_JOBS),
                        ExecutionJob.deadline_at > func.now(),
                        ExecutionAttempt.lease_expires_at <= func.now(),
                    )
                    .order_by(ExecutionAttempt.lease_expires_at, ExecutionAttempt.id)
                    .with_for_update(skip_locked=True)
                    .limit(remaining)
                ).all()
                for attempt, job in abandoned:
                    attempt.status = "lost"
                    attempt.finished_at = func.now()
                    attempt.terminal_outcome = "lease_expired"
                    job.status = "recovery_required"
                    job.updated_at = func.now()
                    self._event(session, job, attempt, "execution_lease_lost")
                    changed += 1
        from backend.classification import AttemptClassificationService
        AttemptClassificationService(self._database).recover_failed_attempts(limit)
        return changed

    def run(self, poll_seconds: int = 2, recovery_limit: int = 100) -> None:
        if not 1 <= poll_seconds <= 60:
            raise ValueError("poll interval is invalid")
        try:
            while True:
                self.sweep(recovery_limit)
                if not self.process_once():
                    time.sleep(poll_seconds)
        finally:
            self._database.dispose()

    def _transition(
        self,
        claim: ClaimedAttempt,
        source: str,
        destination: str,
        job_destination: Optional[str],
        category: str,
    ) -> bool:
        with self._database.session_scope() as session:
            attempt_result = session.execute(
                update(ExecutionAttempt)
                .where(*_lease_predicates(claim), ExecutionAttempt.status == source)
                .values(status=destination, started_at=func.now() if destination == "starting" else ExecutionAttempt.started_at)
            )
            if attempt_result.rowcount != 1:
                return False
            if job_destination is not None:
                job_result = session.execute(
                    update(ExecutionJob)
                    .where(
                        ExecutionJob.id == claim.job_id,
                        ExecutionJob.project_id == claim.project_id,
                        ExecutionJob.status == "claimed",
                    )
                    .values(status=job_destination, updated_at=func.now())
                )
                if job_result.rowcount != 1:
                    raise RuntimeError("lease ownership lost")
            self._event_by_claim(session, claim, category)
            return True

    def _terminal(self, claim: ClaimedAttempt, attempt_status: str, job_status: str, outcome: str, category: str, evidence=()) -> bool:
        with self._database.session_scope() as session:
            attempt_result = session.execute(
                update(ExecutionAttempt)
                .where(*_lease_predicates(claim), ExecutionAttempt.status.in_(_ACTIVE_ATTEMPTS))
                .values(status=attempt_status, finished_at=func.now(), terminal_outcome=outcome)
            )
            if attempt_result.rowcount != 1:
                return False
            job_conditions = [
                ExecutionJob.id == claim.job_id,
                ExecutionJob.project_id == claim.project_id,
                ExecutionJob.status.in_(_ACTIVE_JOBS),
            ]
            if job_status == "cancelled":
                job_conditions.append(ExecutionJob.cancel_requested_at.is_not(None))
            elif job_status in ("succeeded", "failed"):
                # A concurrent cancellation or database deadline wins over a
                # late browser result; a later sweep finalizes it safely.
                job_conditions.extend((
                    ExecutionJob.cancel_requested_at.is_(None),
                    ExecutionJob.deadline_at > func.now(),
                ))
            job_result = session.execute(
                update(ExecutionJob)
                .where(*job_conditions)
                .values(status=job_status, updated_at=func.now())
            )
            if job_result.rowcount != 1:
                raise RuntimeError("lease ownership lost")
            for record in evidence:
                if not isinstance(record, EvidenceRecord) or not record.verify_digest():
                    raise RuntimeError("invalid execution evidence")
                session.add(ExecutionEvidence(
                    id=uuid.uuid4(), project_id=claim.project_id, execution_job_id=claim.job_id,
                    execution_attempt_id=claim.attempt_id, evidence_id=record.evidence_id,
                    schema_version="1.0.0", source=record.source.value,
                    producing_component=record.producing_component, captured_at=record.captured_at,
                    observed_facts=record.to_dict()["observed_facts"], content_digest=record.content_digest,
                ))
            self._event_by_claim(session, claim, category)
            return True

    def _event(self, session, job, attempt, category: str) -> None:
        session.add(
            ExecutionEvent(
                id=uuid.uuid4(), project_id=job.project_id,
                execution_job_id=job.id, execution_attempt_id=attempt.id,
                correlation_id=str(uuid.uuid4()), category=category,
                event_metadata={}, worker_id=self._worker_id,
            )
        )

    def _event_by_claim(self, session, claim: ClaimedAttempt, category: str) -> None:
        session.add(
            ExecutionEvent(
                id=uuid.uuid4(), project_id=claim.project_id,
                execution_job_id=claim.job_id, execution_attempt_id=claim.attempt_id,
                correlation_id=str(uuid.uuid4()), category=category,
                event_metadata={}, worker_id=self._worker_id,
            )
        )


def _lease_expiry_expression(seconds: int):
    return func.now() + bindparam("lease_seconds", value=seconds, type_=Integer()) * text("INTERVAL '1 second'")


def _lease_predicates(claim: ClaimedAttempt):
    return (
        ExecutionAttempt.id == claim.attempt_id,
        ExecutionAttempt.project_id == claim.project_id,
        ExecutionAttempt.execution_job_id == claim.job_id,
        ExecutionAttempt.lease_token == claim.lease_token,
        ExecutionAttempt.lease_expires_at > func.now(),
    )


def main() -> None:
    """Run as ``python -m backend.worker`` with an explicit database URL."""

    from backend.config import load_settings
    from backend.logging import configure_logging

    settings = load_settings()
    if settings.database_url is None:
        raise SystemExit("AEGISQA_DATABASE_URL is required for the worker.")
    fixture_sandbox = None
    if settings.fixture_execution_enabled:
        fixture_sandbox = DockerSandbox(settings.fixture_sandbox_image_id)
    worker = ExecutionWorker(
        DatabaseManager(settings),
        settings.execution_worker_id or "worker-" + uuid.uuid4().hex,
        settings.execution_worker_lease_seconds,
        configure_logging(settings, logger_name="aegisqa.worker." + uuid.uuid4().hex),
        fixture_sandbox=fixture_sandbox,
        fixture_image_id=settings.fixture_image_id,
    )
    worker.run(settings.execution_worker_poll_seconds, settings.execution_worker_recovery_limit)


if __name__ == "__main__":
    main()
