"""Contracts shared by interactive and batch RAG execution."""

from typing import List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Запрос к RAG-ассистенту."""

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)
    session_id: Optional[str] = None
    score_threshold: Optional[float] = Field(default=None, ge=-1, le=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=100)
    max_context_chars: Optional[int] = Field(default=None, ge=1, le=100000)
    max_attempts: int = Field(1, ge=1, le=3)
    question_set_version_id: Optional[UUID] = None
    question_example_id: Optional[UUID] = None


class ChatResponse(BaseModel):
    """Ответ RAG-ассистента."""

    answer: str
    session_id: str
    sources: List[str]
    retrieval_status: Literal["success", "no_context"]
    applied_settings: dict
    context_chars: int
    run_id: UUID
    configuration_version_id: UUID
