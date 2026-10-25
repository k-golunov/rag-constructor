"""Durable batch job outbox and cooperative cancellation.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("batch_runs") as batch:
        batch.add_column(
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("active_job_id", UUID(as_uuid=True), nullable=True))
    op.create_table(
        "batch_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("batch_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("batch_question_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("execution_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_batch_jobs_batch_run_id", "batch_jobs", ["batch_run_id"])
    op.create_index("ix_batch_jobs_status", "batch_jobs", ["status"])


def downgrade():
    op.drop_index("ix_batch_jobs_status", table_name="batch_jobs")
    op.drop_index("ix_batch_jobs_batch_run_id", table_name="batch_jobs")
    op.drop_table("batch_jobs")
    with op.batch_alter_table("batch_runs") as batch:
        batch.drop_column("active_job_id")
        batch.drop_column("cancel_requested")
