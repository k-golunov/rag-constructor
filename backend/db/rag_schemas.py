"""Contracts shared by interactive and batch RAG execution."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Запрос к RAG-ассистенту."""

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)
    session_id: str | None = None
    score_threshold: float | None = Field(default=None, ge=-1, le=1)
    top_k: int | None = Field(default=None, ge=1, le=100)
    max_context_chars: int | None = Field(default=None, ge=1, le=100000)
    max_attempts: int = Field(1, ge=1, le=3)
    question_set_version_id: UUID | None = None
    question_example_id: UUID | None = None


class ChatResponse(BaseModel):
    """Ответ RAG-ассистента."""

    answer: str
    session_id: str
    sources: list[str]
    retrieval_status: Literal["success", "no_context"]
    applied_settings: dict
    context_chars: int
    run_id: UUID
    configuration_version_id: UUID
