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

from pydantic import BaseModel, Field


# ===========================================================================
# Project
# ===========================================================================

class ProjectCreate(BaseModel):
    """Данные для создания нового проекта."""

    name: str = Field(..., min_length=1, max_length=255, examples=["Мой RAG-проект"])
    chunk_size: int = Field(800, ge=100, le=8000, description="Размер чанка в символах")
    chunk_overlap: int = Field(100, ge=0, le=2000, description="Перекрытие между чанками")
    embedding_model: str = Field(..., examples=["text-embedding-3-small"])
    llm_model: str = Field(..., examples=["gpt-4o-mini"])
    system_prompt: str = Field(
        "Вы полезный ассистент, отвечающий на вопросы по документам.",
        description="Системный промпт для LLM",
    )


class ProjectUpdate(BaseModel):
    """Частичное обновление проекта (все поля опциональны)."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    chunk_size: Optional[int] = Field(None, ge=100, le=8000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=2000)
    embedding_model: Optional[str] = None
    llm_model: Optional[str] = None
    system_prompt: Optional[str] = None


class ProjectResponse(BaseModel):
    """Полное представление проекта в ответе API."""

    id: UUID
    name: str
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    llm_model: str
    system_prompt: str
    created_at: datetime

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
    """Данные, передаваемые при регистрации нового источника (до загрузки файла)."""

    project_id: UUID
    file_name: str = Field(..., min_length=1, max_length=512)
    file_path: str = Field(..., min_length=1, max_length=1024)


class DataSourceStatusUpdate(BaseModel):
    """Обновление статуса и результатов обработки источника."""

    status: DataSourceStatus
    chunks_count: Optional[int] = Field(None, ge=0)
    error_message: Optional[str] = None


class DataSourceResponse(BaseModel):
    """Полное представление источника данных в ответе API."""

    id: UUID
    project_id: UUID
    file_name: str
    file_path: str
    status: DataSourceStatus
    chunks_count: int
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class DataSourceListResponse(BaseModel):
    """Список источников данных проекта."""

    total: int
    items: List[DataSourceResponse]


# ===========================================================================
# ChatHistory
# ===========================================================================

ChatRole = Literal["user", "assistant"]


class ChatMessageCreate(BaseModel):
    """Одно сообщение для сохранения в историю чата."""

    project_id: UUID
    session_id: UUID
    role: ChatRole
    content: str = Field(..., min_length=1)


class ChatMessageResponse(BaseModel):
    """Представление сообщения из истории чата."""

    id: int
    project_id: UUID
    session_id: UUID
    role: ChatRole
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatHistoryResponse(BaseModel):
    """История чата в рамках одной сессии."""

    session_id: UUID
    project_id: UUID
    messages: List[ChatMessageResponse]
