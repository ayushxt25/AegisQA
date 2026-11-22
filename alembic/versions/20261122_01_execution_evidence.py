"""Add append-only attempt-linked browser evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261122_01"
down_revision = "20261121_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0.0"),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("producing_component", sa.String(128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_facts", postgresql.JSONB(), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["project_id", "execution_job_id", "execution_attempt_id"], ["execution_attempts.project_id", "execution_attempts.execution_job_id", "execution_attempts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("execution_attempt_id", "evidence_id", name="uq_execution_evidence_attempt_id"),
        sa.CheckConstraint("jsonb_typeof(observed_facts) = 'object' AND octet_length(observed_facts::text) <= 4096", name="ck_execution_evidence_facts"),
        sa.CheckConstraint("char_length(evidence_id) > 0 AND char_length(content_digest) = 64", name="ck_execution_evidence_identity"),
    )
    op.create_index("ix_execution_evidence_attempt", "execution_evidence", ["project_id", "execution_attempt_id", "captured_at", "id"])


def downgrade():
    op.drop_index("ix_execution_evidence_attempt", table_name="execution_evidence")
    op.drop_table("execution_evidence")
