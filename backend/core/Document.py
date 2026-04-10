from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import uuid

class Document(BaseModel):
    """Универсальный объект для передачи между парсером, эмбеддером и векторной БД."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    text: str
    metadata: Dict[str, Any] = {}
    embedding: Optional[List[float]] = None