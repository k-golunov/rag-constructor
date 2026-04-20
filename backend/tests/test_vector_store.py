"""
Юнит-тесты для QdrantStore.

Qdrant-клиент полностью мокируется — реальный сервер не нужен.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.Document import Document
from backend.core.vector_store.qdrant_store import QdrantStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> tuple[QdrantStore, AsyncMock]:
    """Создаёт QdrantStore с замоканным AsyncQdrantClient."""
    with patch(
        "backend.core.vector_store.qdrant_store.AsyncQdrantClient", autospec=True
    ) as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        store = QdrantStore(url="http://localhost:6333")
    store._client = mock_client
    return store, mock_client


def _qdrant_hit(hit_id: str, payload: dict) -> MagicMock:
    """Создаёт объект, имитирующий ScoredPoint из qdrant-client."""
    hit = MagicMock()
    hit.id = hit_id
    hit.payload = payload
    return hit


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------

class TestCreateCollection:
    @pytest.mark.asyncio
    async def test_creates_when_not_exists(self):
        store, client = _make_store()
        client.collection_exists.return_value = False

        await store.create_collection("my-collection", vector_size=1536)

        client.create_collection.assert_awaited_once()
        call_kwargs = client.create_collection.call_args.kwargs
        assert call_kwargs["collection_name"] == "my-collection"

    @pytest.mark.asyncio
    async def test_skips_when_already_exists(self):
        store, client = _make_store()
        client.collection_exists.return_value = True

        await store.create_collection("my-collection", vector_size=1536)

        client.create_collection.assert_not_awaited()


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    @pytest.mark.asyncio
    async def test_upsert_converts_documents_to_points(self):
        store, client = _make_store()
        docs = [
            Document(text="chunk one", metadata={"source": "doc.pdf"}, embedding=[0.1, 0.2]),
            Document(text="chunk two", metadata={}, embedding=[0.3, 0.4]),
        ]

        await store.upsert("col", docs)

        client.upsert.assert_awaited_once()
        points = client.upsert.call_args.kwargs["points"]
        assert len(points) == 2
        assert points[0].payload["text"] == "chunk one"
        assert points[0].payload["source"] == "doc.pdf"
        assert points[1].payload["text"] == "chunk two"

    @pytest.mark.asyncio
    async def test_skips_documents_without_embedding(self):
        store, client = _make_store()
        docs = [
            Document(text="no vector", metadata={}),  # embedding=None
            Document(text="has vector", metadata={}, embedding=[0.1, 0.2]),
        ]

        await store.upsert("col", docs)

        points = client.upsert.call_args.kwargs["points"]
        assert len(points) == 1
        assert points[0].payload["text"] == "has vector"

    @pytest.mark.asyncio
    async def test_does_not_call_upsert_when_no_valid_documents(self):
        store, client = _make_store()
        docs = [Document(text="no vector", metadata={})]

        await store.upsert("col", docs)

        client.upsert.assert_not_awaited()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_documents_from_results(self):
        store, client = _make_store()
        client.search.return_value = [
            _qdrant_hit("id-1", {"text": "first chunk", "source": "a.pdf"}),
            _qdrant_hit("id-2", {"text": "second chunk"}),
        ]

        results = await store.search("col", query_vector=[0.1, 0.2], limit=2)

        assert len(results) == 2
        assert results[0].id == "id-1"
        assert results[0].text == "first chunk"
        assert results[0].metadata == {"source": "a.pdf"}
        assert results[0].embedding is None

    @pytest.mark.asyncio
    async def test_passes_limit_to_client(self):
        store, client = _make_store()
        client.search.return_value = []

        await store.search("col", query_vector=[0.5], limit=10)

        call_kwargs = client.search.call_args.kwargs
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_results(self):
        store, client = _make_store()
        client.search.return_value = []

        results = await store.search("col", query_vector=[0.1])

        assert results == []
