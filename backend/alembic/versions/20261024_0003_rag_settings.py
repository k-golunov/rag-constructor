"""Persist retrieval settings and the configuration applied to saved answers.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("top_k", sa.Integer(), nullable=False, server_default="5")
    )
    op.add_column(
        "projects",
        sa.Column(
            "max_context_chars", sa.Integer(), nullable=False, server_default="2000"
        ),
    )
    op.add_column("chat_history", sa.Column("rag_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_history", "rag_metadata")
    op.drop_column("projects", "max_context_chars")
    op.drop_column("projects", "top_k")
