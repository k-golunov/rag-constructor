"""Independent immutable checks of saved evaluator inputs.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "result_id",
            UUID(as_uuid=True),
            sa.ForeignKey("batch_question_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "execution_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("execution_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("evaluator_descriptor", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evaluation_checks_result_id", "evaluation_checks", ["result_id"]
    )


def downgrade():
    op.drop_index("ix_evaluation_checks_result_id", table_name="evaluation_checks")
    op.drop_table("evaluation_checks")
