from abc import ABC, abstractmethod

from backend.core.Document import Document


class BaseVectorStore(ABC):
    @abstractmethod
    async def validate_collection(self, collection_name: str, vector_size: int) -> None:
        """Проверяет наличие и совместимость уже построенного индекса."""

    @abstractmethod
    async def create_collection(self, collection_name: str, vector_size: int) -> None:
        """Создаёт коллекцию, если она не существует."""

    @abstractmethod
    async def upsert(self, collection_name: str, documents: list[Document]) -> None:
        """Вставляет или обновляет документы (чанки) в коллекции."""

    @abstractmethod
    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: float | None = None,
    ) -> list[Document]:
        """Возвращает список наиболее похожих документов."""
