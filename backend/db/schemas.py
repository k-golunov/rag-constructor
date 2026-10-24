"""
Pydantic-схемы для валидации входящих данных и формирования ответов API.

Соглашения об именовании:
  - <Model>Create  — тело запроса при создании записи
  - <Model>Update  — тело запроса при частичном обновлении
  - <Model>Response — тело ответа (включает поля, генерируемые БД: id, created_at и т.д.)
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


# ===========================================================================
# Project
# ===========================================================================

SplitBy = Literal["paragraphs"]
ChunkingStrategy = Literal["recursive"]


class ProjectCreate(BaseModel):
    """Данные для создания нового проекта."""

    model_config = {"extra": "forbid"}

    # ── Основное ────────────────────────────────────────────────────────────
    name: str = Field(..., min_length=1, max_length=255, examples=["Мой RAG-проект"])

    # ── Чанкинг ─────────────────────────────────────────────────────────────
    chunk_size: int = Field(800, ge=100, le=8000, description="Размер чанка в символах")
    chunk_overlap: int = Field(
        100, ge=0, le=2000, description="Перекрытие между чанками"
    )
    split_by: SplitBy = Field("paragraphs", description="Способ разбивки текста")
    chunking_strategy: ChunkingStrategy = Field(
        "recursive", description="Стратегия чанкинга"
    )
    extract_tables: Literal[False] = Field(False, description="Пока не поддерживается")
    top_k: int = Field(5, ge=1, le=100)
    max_context_chars: int = Field(2000, ge=1, le=100000)

    # ── Эмбеддинги ──────────────────────────────────────────────────────────
    embedding_model: str = Field(..., examples=["text-embedding-3-small"])
    embedding_dimension: int = Field(1536, ge=1, description="Размерность векторов")
    embedding_api_key: Optional[str] = Field(None, max_length=255)
    embedding_api_url: Optional[str] = Field(None, max_length=512)

    # ── LLM ─────────────────────────────────────────────────────────────────
    llm_model: str = Field(..., examples=["gpt-4o-mini"])
    llm_api_key: Optional[str] = Field(None, max_length=255)
    llm_api_url: Optional[str] = Field(None, max_length=512)

    # ── Промпт ──────────────────────────────────────────────────────────────
    system_prompt: str = Field(
        "Вы полезный ассистент, отвечающий на вопросы по документам.",
        description="Системный промпт для LLM",
    )

    @field_validator("embedding_api_url", "llm_api_url", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        """Преобразует пустую строку в None для URL-полей."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @model_validator(mode="after")
    def validate_chunk_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap должен быть меньше chunk_size")
        return self


class ProjectUpdate(BaseModel):
    """Частичное обновление проекта — все поля опциональны."""

    model_config = {"extra": "forbid"}

    name: Optional[str] = Field(None, min_length=1, max_length=255)

    chunk_size: Optional[int] = Field(None, ge=100, le=8000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=2000)
    split_by: Optional[SplitBy] = None
    chunking_strategy: Optional[ChunkingStrategy] = None
    extract_tables: Optional[Literal[False]] = None
    top_k: Optional[int] = Field(None, ge=1, le=100)
    max_context_chars: Optional[int] = Field(None, ge=1, le=100000)

    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = Field(None, ge=1)
    embedding_api_key: Optional[str] = Field(None, max_length=255)
    embedding_api_url: Optional[str] = Field(None, max_length=512)

    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = Field(None, max_length=255)
    llm_api_url: Optional[str] = Field(None, max_length=512)

    system_prompt: Optional[str] = None

    @field_validator("embedding_api_url", "llm_api_url", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


class ProjectResponse(BaseModel):
    """Полное представление проекта в ответе API."""

    id: UUID
    name: str
    created_at: datetime

    chunk_size: int
    chunk_overlap: int
    split_by: str
    chunking_strategy: str
    extract_tables: bool

    top_k: int
    max_context_chars: int

    embedding_model: str
    embedding_dimension: int
    embedding_api_key: Optional[str]
    embedding_api_url: Optional[str]

    llm_model: str
    llm_api_key: Optional[str]
    llm_api_url: Optional[str]

    system_prompt: str

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    """Список проектов с пагинацией."""

    total: int
    items: List[ProjectResponse]


# ===========================================================================
# DataSource
# ===========================================================================

DataSourceStatus = Literal["pending", "processing", "completed", "failed"]


class DataSourceCreate(BaseModel):
    project_id: UUID
    file_name: str = Field(..., min_length=1, max_length=512)
    file_path: str = Field(..., min_length=1, max_length=1024)


class DataSourceStatusUpdate(BaseModel):
    status: DataSourceStatus
    chunks_count: Optional[int] = Field(None, ge=0)
    error_message: Optional[str] = None


class DataSourceResponse(BaseModel):
    id: UUID
    project_id: UUID
    file_name: str
    file_path: str
    status: DataSourceStatus
    chunks_count: int
    error_message: Optional[str]
    created_at: datetime
    document_version_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


class DataSourceListResponse(BaseModel):
    total: int
    items: List[DataSourceResponse]


# ===========================================================================
# ChatHistory
# ===========================================================================

ChatRole = Literal["user", "assistant"]


class ChatMessageCreate(BaseModel):
    project_id: UUID
    session_id: UUID
    role: ChatRole
    content: str = Field(..., min_length=1)


class ChatMessageResponse(BaseModel):
    id: int
    project_id: UUID
    session_id: UUID
    role: ChatRole
    content: str
    created_at: datetime
    rag_metadata: Optional[dict] = None

    model_config = {"from_attributes": True}

    model_config = {"from_attributes": True}


class ChatHistoryResponse(BaseModel):
    session_id: UUID
    project_id: UUID
    messages: List[ChatMessageResponse]


# ===========================================================================
# Chat / Generate
# ===========================================================================


class GenerateRequest(BaseModel):
    """Запрос на генерацию ответа LLM с учётом найденного контекста."""

    model_config = {"extra": "forbid"}

    max_context_chars: Optional[int] = Field(None, ge=1, le=100000)
    max_attempts: int = Field(1, ge=1, le=3)
    question_set_version_id: Optional[UUID] = None
    question_example_id: Optional[UUID] = None

    query: str = Field(..., min_length=1, description="Вопрос пользователя")
    context: str = Field(default="", description="Найденные чанки (строка)")
    session_id: Optional[UUID] = Field(
        None, description="ID сессии для сохранения истории"
    )
    history: Optional[List[dict]] = Field(
        None,
        description='История диалога: [{"role": "user"|"assistant", "content": "..."}]',
    )


class GenerateResponse(BaseModel):
    """Ответ LLM на вопрос пользователя."""

    answer: str
    run_id: UUID
    applied_settings: dict
    context_chars: int
    configuration_version_id: UUID
