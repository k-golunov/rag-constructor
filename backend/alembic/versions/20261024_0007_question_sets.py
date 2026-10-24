"""Immutable question datasets and links from evaluation runs.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "question_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_question_sets_project_id", "question_sets", ["project_id"])
    op.create_table(
        "question_set_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "question_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("question_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("documents_sha256", sa.String(64), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("question_set_id", "number"),
    )
    op.create_index(
        "ix_question_set_versions_question_set_id",
        "question_set_versions",
        ["question_set_id"],
    )
    with op.batch_alter_table("execution_runs") as batch:
        batch.add_column(
            sa.Column("question_set_version_id", UUID(as_uuid=True), nullable=True)
        )
        batch.add_column(
            sa.Column("question_example_id", UUID(as_uuid=True), nullable=True)
        )
        batch.create_foreign_key(
            "fk_runs_question_set_version",
            "question_set_versions",
            ["question_set_version_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("execution_runs") as batch:
        batch.drop_constraint("fk_runs_question_set_version", type_="foreignkey")
        batch.drop_column("question_example_id")
        batch.drop_column("question_set_version_id")
    op.drop_index(
        "ix_question_set_versions_question_set_id", table_name="question_set_versions"
    )
    op.drop_table("question_set_versions")
    op.drop_index("ix_question_sets_project_id", table_name="question_sets")
    op.drop_table("question_sets")
