from abc import ABC, abstractmethod
from typing import List
from backend.core.Document import Document


class BaseVectorStore(ABC):
    @abstractmethod
    async def create_collection(self, collection_name: str, vector_size: int) -> None:
        """Создаёт коллекцию, если она не существует."""
        pass

    @abstractmethod
    async def upsert(self, collection_name: str, documents: List[Document]) -> None:
        """Вставляет или обновляет документы (чанки) в коллекции."""
        pass

    @abstractmethod
    async def search(
        self, collection_name: str, query_vector: List[float], limit: int = 5
    ) -> List[Document]:
        """Возвращает список наиболее похожих документов."""
        pass
