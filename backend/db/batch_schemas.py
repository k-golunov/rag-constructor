"""Validated batch execution requests; only implemented settings are accepted."""

from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class VariantSettings(BaseModel):
    model_config = {"extra": "forbid"}
    top_k: int | None = Field(None, ge=1, le=100)
    max_context_chars: int | None = Field(None, ge=1, le=100000)
    score_threshold: float | None = Field(None, ge=-1, le=1)
    max_attempts: int = Field(1, ge=1, le=3)
    chunk_size: int | None = Field(None, ge=100, le=8000)
    chunk_overlap: int | None = Field(None, ge=0, le=2000)
    embedding_model: str | None = Field(None, min_length=1, max_length=100)
    embedding_dimension: int | None = Field(None, ge=1)
    llm_model: str | None = Field(None, min_length=1, max_length=100)
    system_prompt: str | None = None


class VariantInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=255)
    configuration_version_id: UUID | None = None
    settings: VariantSettings = Field(default_factory=VariantSettings)


class BatchCreate(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=255)
    question_set_version_id: UUID
    split: Literal["tuning", "test"]
    example_ids: list[UUID] | None = Field(None, min_length=1, max_length=10000)
    variants: list[VariantInput] = Field(min_length=1, max_length=20)
    repeats: int = Field(1, ge=1, le=10)
    preview_sha256: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")
