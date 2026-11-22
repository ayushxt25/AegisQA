"""Initial PostgreSQL identity and project persistence schema."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata used exclusively by Alembic migrations."""


class MembershipRole(str, enum.Enum):
    ADMINISTRATOR = "administrator"
    OPERATOR = "operator"
    REVIEWER = "reviewer"


class ExecutionJobStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    RECOVERY_REQUIRED = "recovery_required"


class ExecutionAttemptStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LOST = "lost"
    FINALIZATION_FAILED = "finalization_failed"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        UniqueConstraint("identity_subject", name="uq_users_identity_subject"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_authenticated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
        UniqueConstraint("slug", name="uq_projects_slug"),
        Index("ix_projects_created_by_user_id", "created_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('administrator', 'operator', 'reviewer')",
            name="ck_project_memberships_role",
        ),
        Index(
            "uq_active_project_membership",
            "project_id",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserTarget(Base):
    __tablename__ = "browser_targets"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_browser_targets_project_id_id"),
        UniqueConstraint("project_id", "name", name="uq_browser_targets_project_name"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_browser_targets_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class BrowserTargetVersion(Base):
    __tablename__ = "browser_target_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_browser_target_versions_project_id_id"),
        UniqueConstraint("browser_target_id", "version", name="uq_browser_target_versions_target_version"),
        CheckConstraint("approval_state IN ('pending', 'approved', 'revoked')", name="ck_browser_target_versions_approval"),
        CheckConstraint("jsonb_typeof(path_prefixes) = 'array'", name="ck_browser_target_versions_paths"),
        ForeignKeyConstraint(["project_id", "browser_target_id"], ["browser_targets.project_id", "browser_targets.id"], ondelete="RESTRICT"),
        Index("ix_browser_target_versions_dispatch", "project_id", "approval_state"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    browser_target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    origin: Mapped[str] = mapped_column(String(512), nullable=False)
    path_prefixes: Mapped[list] = mapped_column(JSONB, nullable=False)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)


class TestDefinition(Base):
    __tablename__ = "test_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_test_definitions_project_id_id"),
        UniqueConstraint("project_id", "name", name="uq_test_definitions_project_name"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_test_definitions_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class TestDefinitionVersion(Base):
    __tablename__ = "test_definition_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_test_definition_versions_project_id_id"),
        UniqueConstraint("test_definition_id", "version", name="uq_test_definition_versions_definition_version"),
        CheckConstraint("jsonb_typeof(action_policy) = 'object'", name="ck_test_definition_versions_policy"),
        CheckConstraint("jsonb_typeof(assertion_contract) = 'object'", name="ck_test_definition_versions_assertion"),
        ForeignKeyConstraint(["project_id", "test_definition_id"], ["test_definitions.project_id", "test_definitions.id"], ondelete="RESTRICT"),
        Index("ix_test_definition_versions_project_created", "project_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    test_definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    objective: Mapped[str] = mapped_column(String(1000), nullable=False)
    action_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assertion_contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class ExecutionJob(Base):
    __tablename__ = "execution_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_execution_jobs_project_id_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_execution_jobs_project_idempotency"),
        UniqueConstraint("retry_of_execution_job_id", name="uq_execution_jobs_retry_parent"),
        CheckConstraint("status IN ('queued','claimed','running','succeeded','failed','cancelled','timed_out','recovery_required')", name="ck_execution_jobs_status"),
        CheckConstraint("jsonb_typeof(request_snapshot) = 'object'", name="ck_execution_jobs_request_object"),
        CheckConstraint("request_schema_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name="ck_execution_jobs_request_schema_version"),
        CheckConstraint("request_snapshot <> '{}'::jsonb", name="ck_execution_jobs_request_nonempty"),
        ForeignKeyConstraint(["project_id", "retry_of_execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id", "browser_target_version_id"], ["browser_target_versions.project_id", "browser_target_versions.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id", "test_definition_version_id"], ["test_definition_versions.project_id", "test_definition_versions.id"], ondelete="RESTRICT"),
        Index("ix_execution_jobs_queue", "status", "created_at"),
        Index("ix_execution_jobs_project_created", "project_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    request_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retry_of_execution_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    browser_target_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    test_definition_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint("execution_job_id", name="uq_execution_attempts_job"),
        UniqueConstraint("project_id", "execution_job_id", "id", name="uq_execution_attempts_project_job_id"),
        CheckConstraint("attempt_number = 1", name="ck_execution_attempts_one_per_job"),
        CheckConstraint("status IN ('queued','claimed','starting','running','succeeded','failed','cancelled','timed_out','lost','finalization_failed')", name="ck_execution_attempts_status"),
        ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        Index("ix_execution_attempts_lease", "status", "lease_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'queued'"))
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "execution_job_id"], ["execution_jobs.project_id", "execution_jobs.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"),
        CheckConstraint("jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 8192", name="ck_execution_events_metadata"),
        CheckConstraint("char_length(correlation_id) > 0 AND char_length(category) > 0", name="ck_execution_events_identity_nonempty"),
        Index("ix_execution_events_history", "project_id", "execution_job_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ExecutionEvidence(Base):
    """Append-only, parent-validated Phase 0 evidence for one durable attempt."""
    __tablename__ = "execution_evidence"
    __table_args__ = (
        UniqueConstraint("execution_attempt_id", "evidence_id", name="uq_execution_evidence_attempt_id"),
        ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"),
        CheckConstraint("jsonb_typeof(observed_facts) = 'object' AND octet_length(observed_facts::text) <= 4096", name="ck_execution_evidence_facts"),
        CheckConstraint("char_length(evidence_id) > 0 AND char_length(content_digest) = 64", name="ck_execution_evidence_identity"),
        Index("ix_execution_evidence_attempt", "project_id", "execution_attempt_id", "captured_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'1.0.0'"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    producing_component: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_facts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ExecutionClassification(Base):
    """One immutable deterministic failure finding for a failed attempt."""
    __tablename__ = "execution_classifications"
    __table_args__ = (
        UniqueConstraint("execution_attempt_id", name="uq_execution_classifications_attempt"),
        ForeignKeyConstraint(
            ["project_id", "execution_job_id", "execution_attempt_id"],
            ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("jsonb_typeof(evidence_references) = 'array' AND octet_length(evidence_references::text) <= 4096", name="ck_execution_classifications_evidence"),
        CheckConstraint("jsonb_typeof(missing_evidence) = 'array' AND octet_length(missing_evidence::text) <= 2048", name="ck_execution_classifications_missing"),
        CheckConstraint("char_length(classifier_version) > 0 AND char_length(applied_rule_id) > 0", name="ck_execution_classifications_identity"),
        Index("ix_execution_classifications_attempt", "project_id", "execution_attempt_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    execution_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_rule_id: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_references: Mapped[list] = mapped_column(JSONB, nullable=False)
    missing_evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
