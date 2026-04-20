"""QdrantStore — реализация BaseVectorStore на базе Qdrant."""

from __future__ import annotations

import uuid
from typing import List, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from backend.core.Document import Document
from backend.core.vector_store.base import BaseVectorStore


class QdrantStore(BaseVectorStore):
    """Адаптер векторного хранилища Qdrant.

    Args:
        url: URL Qdrant-сервера, например ``http://localhost:6333``.
        api_key: API-ключ (опционально, для облачного Qdrant).
    """

    def __init__(self, url: str, api_key: Optional[str] = None) -> None:
        self._client = AsyncQdrantClient(url=url, api_key=api_key)

    async def create_collection(self, collection_name: str, vector_size: int) -> None:
        """Создаёт коллекцию с Distance.COSINE, если она ещё не существует.

        Args:
            collection_name: Имя коллекции (обычно str(project_id)).
            vector_size: Размерность векторов (должна совпадать с embedding_dimension проекта).
        """
        exists = await self._client.collection_exists(collection_name)
        if not exists:
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    async def upsert(self, collection_name: str, documents: List[Document]) -> None:
        """Вставляет документы в коллекцию. Документы без embedding пропускаются.

        Args:
            collection_name: Имя коллекции.
            documents: Список Document. Поле ``embedding`` обязательно для индексации.
        """
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=doc.embedding,
                payload={"text": doc.text, **doc.metadata},
            )
            for doc in documents
            if doc.embedding is not None
        ]
        if points:
            await self._client.upsert(collection_name=collection_name, points=points)

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 5,
    ) -> List[Document]:
        """Возвращает наиболее релевантные документы по косинусному сходству.

        Args:
            collection_name: Имя коллекции.
            query_vector: Вектор запроса.
            limit: Максимальное количество результатов.

        Returns:
            Список Document, отсортированных по убыванию релевантности.
        """
        results = await self._client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
        )
        return [
            Document(
                id=str(r.id),
                text=r.payload.get("text", ""),
                metadata={k: v for k, v in r.payload.items() if k != "text"},
                embedding=None,
            )
            for r in results
        ]
