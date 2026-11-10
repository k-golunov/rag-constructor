import uuid
from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    """Универсальный объект для передачи между парсером, эмбеддером и векторной БД."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    text: str
    metadata: dict[str, Any] = {}
    embedding: list[float] | None = None
    score: float | None = None
