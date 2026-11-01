"""Pinned batch runs, variants, question results and technical attempts.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def identifier(
    name="id", target=None, nullable=False, primary_key=False, ondelete=None
):
    args = [name, UUID(as_uuid=True)]
    if target:
        args.append(sa.ForeignKey(target, ondelete=ondelete))
    return sa.Column(*args, nullable=nullable, primary_key=primary_key)


def upgrade():
    op.create_table(
        "batch_runs",
        identifier(primary_key=True),
        identifier("project_id", "projects.id", ondelete="CASCADE"),
        identifier("question_set_version_id", "question_set_versions.id"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        identifier("rerun_of_id", "batch_runs.id", nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_batch_runs_project_id", "batch_runs", ["project_id"])
    op.create_table(
        "batch_variants",
        identifier(primary_key=True),
        identifier("batch_run_id", "batch_runs.id", ondelete="CASCADE"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        identifier("configuration_version_id", "configuration_versions.id"),
        identifier("index_version_id", "index_versions.id"),
        sa.Column("project_snapshot", sa.JSON(), nullable=False),
        sa.Column("applied_settings", sa.JSON(), nullable=False),
        sa.UniqueConstraint("batch_run_id", "position"),
    )
    op.create_index(
        "ix_batch_variants_batch_run_id", "batch_variants", ["batch_run_id"]
    )
    op.create_table(
        "batch_question_results",
        identifier(primary_key=True),
        identifier("variant_id", "batch_variants.id", ondelete="CASCADE"),
        identifier("question_example_id"),
        sa.Column("question_position", sa.Integer(), nullable=False),
        sa.Column("repeat_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("variant_id", "question_example_id", "repeat_number"),
    )
    op.create_index(
        "ix_batch_question_results_variant_id", "batch_question_results", ["variant_id"]
    )
    op.create_table(
        "batch_result_attempts",
        identifier(primary_key=True),
        identifier("result_id", "batch_question_results.id", ondelete="CASCADE"),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        identifier("execution_run_id", "execution_runs.id", ondelete="CASCADE"),
        sa.UniqueConstraint("result_id", "number"),
        sa.UniqueConstraint("execution_run_id"),
    )
    op.create_index(
        "ix_batch_result_attempts_result_id", "batch_result_attempts", ["result_id"]
    )


def downgrade():
    for name, field in (
        ("batch_result_attempts", "result_id"),
        ("batch_question_results", "variant_id"),
        ("batch_variants", "batch_run_id"),
        ("batch_runs", "project_id"),
    ):
        op.drop_index(f"ix_{name}_{field}", table_name=name)
        op.drop_table(name)
