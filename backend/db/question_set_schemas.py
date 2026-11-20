from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: UUID | None = None
    file_name: str | None = Field(None, min_length=1, max_length=512)
    quote: str = Field(min_length=1)
    start_char: int | None = Field(None, ge=0)
    end_char: int | None = Field(None, ge=1)

    @field_validator("quote")
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError("Выдержка не может состоять из пробелов.")
        return value

    @model_validator(mode="after")
    def has_document(self):
        if self.document_version_id is None and self.file_name is None:
            raise ValueError("Укажите document_version_id или file_name документа.")
        return self


class ExampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: UUID = Field(default_factory=uuid4)
    question: str = Field(min_length=1)
    reference_answer: str | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)
    category: str | None = Field(None, max_length=128)
    unanswerable: bool = False
    split: Literal["tuning", "test"] = "tuning"
    explanation: str | None = Field(None, max_length=2000)

    @field_validator("reference_answer", "category", "explanation")
    @classmethod
    def empty_is_unknown(cls, value):
        return value or None


class SetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    examples: list[ExampleInput] = Field(default_factory=list, max_length=10000)


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: UUID
    examples: list[ExampleInput] = Field(max_length=10000)
    refresh_documents: bool = False


class ExampleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: UUID
    example: ExampleInput


class ExampleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_version_id: UUID
    question: str | None = Field(None, min_length=1)
    reference_answer: str | None = None
    evidence: list[EvidenceInput] | None = None
    category: str | None = Field(None, max_length=128)
    unanswerable: bool | None = None
    split: Literal["tuning", "test"] | None = None
    explanation: str | None = Field(None, max_length=2000)


class ExampleReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_version_id: UUID
    status: Literal["pending", "approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=255)
