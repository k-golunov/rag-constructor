"""Initial schema: projects, data_sources, chat_history

Revision ID: 0001
Revises:
Create Date: 2025-04-11 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="800"),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column(
            "system_prompt",
            sa.Text(),
            nullable=False,
            server_default="Вы полезный ассистент, отвечающий на вопросы по документам.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------
    # data_sources
    # ------------------------------------------------------------------
    op.create_table(
        "data_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_data_sources_project_id", "data_sources", ["project_id"])

    # ------------------------------------------------------------------
    # chat_history
    # ------------------------------------------------------------------
    op.create_table(
        "chat_history",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_history_project_id", "chat_history", ["project_id"])
    op.create_index("ix_chat_history_session_id", "chat_history", ["session_id"])


def downgrade() -> None:
    op.drop_table("chat_history")
    op.drop_index("ix_data_sources_project_id", table_name="data_sources")
    op.drop_table("data_sources")
    op.drop_table("projects")
