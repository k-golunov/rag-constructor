from sqlalchemy import (
    Boolean,
    Column,
    String,
    Integer,
    DateTime,
    Text,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from backend.db.database import Base


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
class Project(Base):
    """Проект RAG-конструктора с полным набором настроек пайплайна."""

    __tablename__ = "projects"

    # ── Идентификация ────────────────────────────────────────────────────────
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    name = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Чанкинг ─────────────────────────────────────────────────────────────
    chunk_size = Column(Integer, default=800, nullable=False)
    chunk_overlap = Column(Integer, default=100, nullable=False)
    split_by = Column(String(50), default="paragraphs", nullable=False)
    chunking_strategy = Column(String(50), default="recursive", nullable=False)
    extract_tables = Column(Boolean, default=False, nullable=False)

    # ── Эмбеддинги ──────────────────────────────────────────────────────────
    embedding_model = Column(String(100), nullable=False)
    embedding_dimension = Column(Integer, default=1536, nullable=False)
    embedding_api_key = Column(String(255), nullable=True)
    embedding_api_url = Column(String(512), nullable=True)

    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_model = Column(String(100), nullable=False)
    llm_api_key = Column(String(255), nullable=True)
    llm_api_url = Column(String(512), nullable=True)

    # ── Промпт ──────────────────────────────────────────────────────────────
    system_prompt = Column(
        Text,
        nullable=False,
        default="Вы полезный ассистент, отвечающий на вопросы по документам.",
    )

    # ── Связи ────────────────────────────────────────────────────────────────
    data_sources = relationship(
        "DataSource",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    chat_history = relationship(
        "ChatHistory",
        back_populates="project",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------
class DataSource(Base):
    """Загруженный файл / источник данных, привязанный к проекту."""

    __tablename__ = "data_sources"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(512), nullable=False)
    file_path = Column(String(1024), nullable=False)
    # Допустимые статусы: pending | processing | completed | failed
    status = Column(String(20), nullable=False, default="pending")
    chunks_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="data_sources")


# ---------------------------------------------------------------------------
# ChatHistory
# ---------------------------------------------------------------------------
class ChatHistory(Base):
    """Сообщение в истории чата в рамках сессии проекта."""

    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    # Допустимые роли: user | assistant
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="chat_history")
