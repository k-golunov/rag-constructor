"""Persist execution settings and statuses without question/answer copies.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("applied_settings", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_execution_runs_project_id", "execution_runs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_execution_runs_project_id", table_name="execution_runs")
    op.drop_table("execution_runs")
