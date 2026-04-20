"""
Интеграционные тесты REST-эндпоинта поиска чанков.

Qdrant-клиент мокируется — реальный Qdrant не нужен.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.core.Document import Document
from backend.db.models import Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_VECTOR = [0.1, 0.2, 0.3]

_PROJECT_DEFAULTS = dict(
    name="Test Project",
    embedding_model="text-embedding-3-small",
    embedding_dimension=3,
    llm_model="gpt-4o-mini",
)


def _create_project(db_session) -> Project:
    project = Project(**_PROJECT_DEFAULTS)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _mock_search(return_value: list[Document]):
    """Патчит QdrantStore.search чтобы вернуть заданный список документов."""
    return patch(
        "backend.api.search.get_vector_store",
        return_value=AsyncMock(search=AsyncMock(return_value=return_value)),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSearchChunks:
    def test_returns_200_with_results(self, client, db_session):
        project = _create_project(db_session)
        docs = [
            Document(id="a1", text="relevant chunk", metadata={"source": "doc.pdf"}),
            Document(id="a2", text="another chunk", metadata={}),
        ]

        with _mock_search(docs):
            resp = client.post(
                f"/api/v1/public/provider/projects/{project.id}/search",
                json={"query_vector": _VALID_VECTOR},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["items"][0]["text"] == "relevant chunk"
        assert body["items"][0]["metadata"] == {"source": "doc.pdf"}

    def test_returns_404_for_unknown_project(self, client, db_session):
        with _mock_search([]):
            resp = client.post(
                f"/api/v1/public/provider/projects/{uuid4()}/search",
                json={"query_vector": _VALID_VECTOR},
            )

        assert resp.status_code == 404

    def test_returns_422_when_query_vector_missing(self, client, db_session):
        project = _create_project(db_session)

        resp = client.post(
            f"/api/v1/public/provider/projects/{project.id}/search",
            json={"limit": 5},
        )

        assert resp.status_code == 422

    def test_returns_empty_items_when_no_results(self, client, db_session):
        project = _create_project(db_session)

        with _mock_search([]):
            resp = client.post(
                f"/api/v1/public/provider/projects/{project.id}/search",
                json={"query_vector": _VALID_VECTOR},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_passes_custom_limit_to_store(self, client, db_session):
        project = _create_project(db_session)
        mock_store = AsyncMock(search=AsyncMock(return_value=[]))

        with patch("backend.api.search.get_vector_store", return_value=mock_store):
            client.post(
                f"/api/v1/public/provider/projects/{project.id}/search",
                json={"query_vector": _VALID_VECTOR, "limit": 20},
            )

        mock_store.search.assert_awaited_once_with(
            collection_name=str(project.id),
            query_vector=_VALID_VECTOR,
            limit=20,
        )

    def test_returns_422_when_limit_out_of_range(self, client, db_session):
        project = _create_project(db_session)

        resp = client.post(
            f"/api/v1/public/provider/projects/{project.id}/search",
            json={"query_vector": _VALID_VECTOR, "limit": 0},
        )

        assert resp.status_code == 422
