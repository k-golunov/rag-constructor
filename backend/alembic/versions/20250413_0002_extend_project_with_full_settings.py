"""Extend project with full settings: chunking, embedding, LLM config

Revision ID: 0002
Revises: 0001
Create Date: 2025-04-13 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Чанкинг ─────────────────────────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column(
            "split_by",
            sa.String(50),
            nullable=False,
            server_default="paragraphs",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "chunking_strategy",
            sa.String(50),
            nullable=False,
            server_default="recursive",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "extract_tables",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # ── Эмбеддинги ──────────────────────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column(
            "embedding_dimension",
            sa.Integer(),
            nullable=False,
            server_default="1536",
        ),
    )
    op.add_column(
        "projects",
        sa.Column("embedding_api_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("embedding_api_url", sa.String(512), nullable=True),
    )

    # ── LLM ─────────────────────────────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column("llm_api_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("llm_api_url", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "llm_api_url")
    op.drop_column("projects", "llm_api_key")
    op.drop_column("projects", "embedding_api_url")
    op.drop_column("projects", "embedding_api_key")
    op.drop_column("projects", "embedding_dimension")
    op.drop_column("projects", "extract_tables")
    op.drop_column("projects", "chunking_strategy")
    op.drop_column("projects", "split_by")
