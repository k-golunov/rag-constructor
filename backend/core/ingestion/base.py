from abc import ABC, abstractmethod
from typing import List
from backend.core.Document import Document


class BaseParser(ABC):
    """Извлекает сырой текст из файла."""

    @abstractmethod
    def parse(self, file_path: str) -> str:
        """Возвращает полный текст документа."""
        pass


class BaseSplitter(ABC):
    """Разбивает текст на чанки."""

    @abstractmethod
    def split_text(self, text: str, metadata: dict) -> List[Document]:
        """
        Принимает сырой текст и базовые метаданные (например, имя файла).
        Возвращает список Document с текстом и обогащёнными метаданными (page, chunk_index).
        """
        pass
