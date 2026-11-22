"""Persist deterministic, attempt-scoped failure classifications."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261123_01"
down_revision = "20261122_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classifier_version", sa.String(64), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("confidence", sa.String(64), nullable=False),
        sa.Column("applied_rule_id", sa.String(16), nullable=False),
        sa.Column("evidence_references", postgresql.JSONB(), nullable=False),
        sa.Column("missing_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("execution_attempt_id", name="uq_execution_classifications_attempt"),
        sa.CheckConstraint("jsonb_typeof(evidence_references) = 'array' AND octet_length(evidence_references::text) <= 4096", name="ck_execution_classifications_evidence"),
        sa.CheckConstraint("jsonb_typeof(missing_evidence) = 'array' AND octet_length(missing_evidence::text) <= 2048", name="ck_execution_classifications_missing"),
        sa.CheckConstraint("char_length(classifier_version) > 0 AND char_length(applied_rule_id) > 0", name="ck_execution_classifications_identity"),
    )
    op.create_index("ix_execution_classifications_attempt", "execution_classifications", ["project_id", "execution_attempt_id"])


def downgrade():
    op.drop_index("ix_execution_classifications_attempt", table_name="execution_classifications")
    op.drop_table("execution_classifications")
