from sqlalchemy import (
    Boolean,
    Column,
    String,
    Integer,
    JSON,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from sqlalchemy import event, inspect

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
    top_k = Column(Integer, default=5, nullable=False)
    max_context_chars = Column(Integer, default=2000, nullable=False)

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
    execution_runs = relationship(
        "ExecutionRun",
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
    document_version_id = Column(
        UUID(as_uuid=True), ForeignKey("document_versions.id"), nullable=True
    )
    is_active = Column(Boolean, nullable=False, default=True)
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
    rag_metadata = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="chat_history")


class ExecutionRun(Base):
    """Настройки, статус и полная трасса принятой попытки выполнения."""

    __tablename__ = "execution_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=True)
    mode = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    stage = Column(String(20), nullable=False)
    applied_settings = Column(JSON, nullable=False)
    configuration_version_id = Column(
        UUID(as_uuid=True), ForeignKey("configuration_versions.id"), nullable=True
    )
    index_version_id = Column(
        UUID(as_uuid=True), ForeignKey("index_versions.id"), nullable=True
    )
    details = Column(JSON, nullable=False, default=dict)
    response_trace = Column(JSON, nullable=True)
    question_set_version_id = Column(
        UUID(as_uuid=True), ForeignKey("question_set_versions.id"), nullable=True
    )
    question_example_id = Column(UUID(as_uuid=True), nullable=True)
    error_code = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="execution_runs")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("project_id", "file_name", "sha256"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    file_path = Column(String(1024), nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IndexVersion(Base):
    __tablename__ = "index_versions"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)

    id = Column(UUID(as_uuid=True), primary_key=True)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fingerprint = Column(String(64), nullable=False)
    collection_name = Column(String(255), nullable=False, unique=True)
    settings = Column(JSON, nullable=False)
    documents = Column(JSON, nullable=False)
    documents_sha256 = Column(String(64), nullable=False)
    document_chunks = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="pending")
    chunks_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConfigurationVersion(Base):
    __tablename__ = "configuration_versions"
    __table_args__ = (UniqueConstraint("project_id", "sha256"),)

    id = Column(UUID(as_uuid=True), primary_key=True)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sha256 = Column(String(64), nullable=False)
    settings = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuestionSet(Base):
    __tablename__ = "question_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuestionSetVersion(Base):
    __tablename__ = "question_set_versions"
    __table_args__ = (UniqueConstraint("question_set_id", "number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("question_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    documents = Column(JSON, nullable=False)
    documents_sha256 = Column(String(64), nullable=False)
    examples = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BatchRun(Base):
    __tablename__ = "batch_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_set_version_id = Column(
        UUID(as_uuid=True), ForeignKey("question_set_versions.id"), nullable=False
    )
    name = Column(String(255), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    selection = Column(JSON, nullable=False)
    repeats = Column(Integer, nullable=False)
    rerun_of_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.id"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    active_job_id = Column(UUID(as_uuid=True), nullable=True)


class BatchJob(Base):
    """Durable outbox item: one logical question, bounded technical attempts."""

    __tablename__ = "batch_jobs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_question_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(20), nullable=False, default="queued", index=True)
    policy = Column(JSON, nullable=False)
    execution_attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BatchVariant(Base):
    __tablename__ = "batch_variants"
    __table_args__ = (UniqueConstraint("batch_run_id", "position"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    configuration_version_id = Column(
        UUID(as_uuid=True), ForeignKey("configuration_versions.id"), nullable=False
    )
    index_version_id = Column(
        UUID(as_uuid=True), ForeignKey("index_versions.id"), nullable=False
    )
    project_snapshot = Column(JSON, nullable=False)
    applied_settings = Column(JSON, nullable=False)


class BatchQuestionResult(Base):
    __tablename__ = "batch_question_results"
    __table_args__ = (
        UniqueConstraint("variant_id", "question_example_id", "repeat_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    variant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_example_id = Column(UUID(as_uuid=True), nullable=False)
    question_position = Column(Integer, nullable=False)
    repeat_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending")


class BatchResultAttempt(Base):
    __tablename__ = "batch_result_attempts"
    __table_args__ = (UniqueConstraint("result_id", "number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_question_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False)
    execution_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )


class EvaluationCheck(Base):
    """Immutable evaluator handoff, separate from collecting an answer."""

    __tablename__ = "evaluation_checks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batch_question_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    input_sha256 = Column(String(64), nullable=False)
    input_snapshot = Column(JSON, nullable=False)
    evaluator_descriptor = Column(JSON, nullable=False)
    output = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


@event.listens_for(EvaluationCheck, "before_update")
@event.listens_for(BatchVariant, "before_update")
@event.listens_for(DocumentVersion, "before_update")
@event.listens_for(ConfigurationVersion, "before_update")
@event.listens_for(QuestionSetVersion, "before_update")
def _immutable_version(mapper, connection, target):
    if any(attribute.history.has_changes() for attribute in inspect(target).attrs):
        raise ValueError("Сохранённая версия неизменяема; создайте новую версию.")


@event.listens_for(IndexVersion, "before_update")
def _immutable_index_definition(mapper, connection, target):
    state = inspect(target)
    immutable_fields = (
        "project_id",
        "fingerprint",
        "collection_name",
        "settings",
        "documents",
        "documents_sha256",
    )
    if any(state.attrs[name].history.has_changes() for name in immutable_fields):
        raise ValueError("Определение индекса неизменяемо; создайте новый индекс.")
    previous = state.attrs.status.history.deleted
    if (previous and previous[0] == "ready") or (
        not previous and target.status == "ready"
    ):
        if any(
            state.attrs[name].history.has_changes()
            for name in ("status", "chunks_count", "document_chunks")
        ):
            raise ValueError("Готовый индекс неизменяем.")


@event.listens_for(BatchRun, "before_update")
@event.listens_for(BatchQuestionResult, "before_update")
def _immutable_batch_definition(mapper, connection, target):
    state = inspect(target)
    mutable = (
        {"status", "finished_at", "cancel_requested", "active_job_id"}
        if isinstance(target, BatchRun)
        else {"status"}
    )
    if any(
        attribute.history.has_changes()
        for attribute in state.attrs
        if attribute.key not in mutable
    ):
        raise ValueError(
            "Определение пакетного запуска неизменяемо; создайте повторный прогон."
        )


@event.listens_for(BatchResultAttempt, "before_update")
def _immutable_batch_attempt(mapper, connection, target):
    _immutable_version(mapper, connection, target)
