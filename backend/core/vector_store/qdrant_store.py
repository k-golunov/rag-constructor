from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from backend.core.Document import Document
from backend.core.vector_store.base import BaseVectorStore


class QdrantVectorStore(BaseVectorStore):
    """Реализация векторного хранилища на базе Qdrant.

    Args:
        url: URL Qdrant-сервера.
        api_key: API-ключ (опционально).
    """

    def __init__(self, url: str, api_key: Optional[str] = None):
        # check_compatibility=False — не дёргать /version при каждом подключении:
        # qdrant/qdrant:latest часто новее pinned qdrant-client, и проверка
        # совместимости лишь сыплет UserWarning, не влияя на работу.
        self._client = QdrantClient(url=url, api_key=api_key, check_compatibility=False)

    async def create_collection(self, collection_name: str, vector_size: int) -> None:
        """Создаёт коллекцию, если она не существует.

        Args:
            collection_name: Имя коллекции.
            vector_size: Размерность векторов.
        """
        if not self._client.collection_exists(collection_name):
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
        else:
            self._validate_dimension(collection_name, vector_size)

    def _validate_dimension(self, collection_name: str, actual_size: int) -> None:
        vectors = self._client.get_collection(collection_name).config.params.vectors
        if not isinstance(vectors, VectorParams):
            raise ValueError("Ожидается коллекция с одним безымянным вектором.")
        if actual_size != vectors.size:
            raise ValueError(
                f"Размерность эмбеддинга {actual_size} не соответствует "
                f"коллекции {collection_name}: {vectors.size}."
            )

    async def validate_collection(self, collection_name: str, vector_size: int) -> None:
        self._validate_dimension(collection_name, vector_size)

    async def upsert(self, collection_name: str, documents: List[Document]) -> None:
        """Вставляет или обновляет документы в коллекции.

        Args:
            collection_name: Имя коллекции.
            documents: Список документов с эмбеддингами.
        """
        for doc in documents:
            if doc.embedding is None:
                raise ValueError(f"У документа {doc.id} отсутствует эмбеддинг.")
        if documents:
            self._validate_dimension(collection_name, len(documents[0].embedding))
            if any(
                len(doc.embedding) != len(documents[0].embedding) for doc in documents
            ):
                raise ValueError("Эмбеддинги документов имеют разные размерности.")
        points = [
            PointStruct(
                id=doc.id,
                vector=doc.embedding,
                payload={"text": doc.text, "metadata": doc.metadata},
            )
            for doc in documents
        ]
        if points:
            self._client.upsert(
                collection_name=collection_name, points=points, wait=True
            )

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[Document]:
        """Возвращает список наиболее похожих документов.

        Args:
            collection_name: Имя коллекции.
            query_vector: Вектор запроса.
            limit: Максимальное количество результатов.

        Returns:
            Список документов, отсортированных по релевантности.
        """
        self._validate_dimension(collection_name, len(query_vector))
        results = self._client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
            score_threshold=score_threshold,
        ).points
        return [
            Document(
                id=str(hit.id),
                text=hit.payload.get("text", ""),
                metadata=hit.payload.get("metadata", {}),
                score=hit.score,
            )
            for hit in results
        ]
