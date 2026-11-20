"""Lease-fenced PostgreSQL worker; browser execution is intentionally absent."""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Integer, and_, bindparam, func, select, text, update

from backend.database import DatabaseManager
from backend.models import ExecutionAttempt, ExecutionEvent, ExecutionJob


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
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker ID is invalid")
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("lease duration is invalid")
        self._database = database
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._logger = logger or logging.getLogger("aegisqa.worker")

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
                .where(
                    ExecutionAttempt.status == "queued",
                    ExecutionJob.status == "queued",
                    ExecutionJob.cancel_requested_at.is_(None),
                    ExecutionJob.deadline_at > func.now(),
                )
                .order_by(ExecutionAttempt.created_at, ExecutionAttempt.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).first()
            if row is None:
                return None
            attempt, job = row
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
        else:
            # Browser execution belongs to a later milestone. This safe terminal
            # outcome prevents an unimplemented executor from holding a lease.
            self.finalize_failed(claim, "executor_unavailable")
        return True

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

    def finalize_failed(self, claim: ClaimedAttempt, reason: str) -> bool:
        if reason != "executor_unavailable":
            raise ValueError("terminal reason is invalid")
        return self._terminal(claim, "failed", "failed", reason, "execution_failed")

    def finalize_timed_out(self, claim: ClaimedAttempt) -> bool:
        return self._terminal(claim, "timed_out", "timed_out", "deadline_exceeded", "execution_timed_out")

    def sweep(self, limit: int = 100) -> int:
        """Finalize expired queued deadlines and abandoned leases without retries."""

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

    def _terminal(self, claim: ClaimedAttempt, attempt_status: str, job_status: str, outcome: str, category: str) -> bool:
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
            job_result = session.execute(
                update(ExecutionJob)
                .where(*job_conditions)
                .values(status=job_status, updated_at=func.now())
            )
            if job_result.rowcount != 1:
                raise RuntimeError("lease ownership lost")
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
    worker = ExecutionWorker(
        DatabaseManager(settings),
        settings.execution_worker_id or "worker-" + uuid.uuid4().hex,
        settings.execution_worker_lease_seconds,
        configure_logging(settings, logger_name="aegisqa.worker." + uuid.uuid4().hex),
    )
    worker.run(settings.execution_worker_poll_seconds, settings.execution_worker_recovery_limit)


if __name__ == "__main__":
    main()
