"""Persist the complete response chain for metrics.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "execution_runs", sa.Column("response_trace", sa.JSON(), nullable=True)
    )


def downgrade():
    op.drop_column("execution_runs", "response_trace")
