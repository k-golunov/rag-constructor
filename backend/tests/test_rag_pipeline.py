"""Upload → real Qdrant → LLM messages → SQL history, without external AI calls.

Set QDRANT_TEST_URL to run the same checks against a disposable server collection.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import QdrantClient

from backend.config import settings
from backend.core.Document import Document
from backend.core.ingestion.pipeline import assign_embeddings
from backend.core.vector_store.qdrant_store import QdrantVectorStore


BASE = "/api/v1/public/provider"
CONTROL = Path(__file__).parent / "fixtures" / "rag_control.txt"
QUESTION = "Какой код доступа к архиву проекта Север?"
ANSWER = "КЕДР-7429"


@pytest.fixture
def rag(client, monkeypatch, tmp_path):
    project = client.post(
        f"{BASE}/projects/",
        json={
            "name": "RAG control",
            "embedding_model": "control",
            "embedding_dimension": 3,
            "llm_model": "control",
            "llm_api_key": "test-key",
        },
    ).json()
    project_id = project["id"]
    collection = f"project_{project_id.replace('-', '_')}"
    url = os.getenv("QDRANT_TEST_URL")
    qdrant = QdrantClient(url=url) if url else QdrantClient(":memory:")
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._client = qdrant
    embedder = SimpleNamespace(embed=AsyncMock(return_value=[[1.0, 0.0, 0.0]]))
    completion = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=ANSWER))]
        )
    )
    ai = MagicMock()
    ai.chat.completions.create = completion
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    for module in ("backend.api.upload", "backend.api.chat"):
        monkeypatch.setattr(f"{module}.build_embedder", lambda *a, **kw: embedder)
        monkeypatch.setattr(f"{module}.build_vector_store", lambda: store)
    monkeypatch.setattr("backend.core.llm.openai_llm.AsyncOpenAI", lambda **kw: ai)
    yield SimpleNamespace(
        client=client,
        project_id=project_id,
        collection=collection,
        store=store,
        qdrant=qdrant,
        embedder=embedder,
        completion=completion,
        chat_url=f"{BASE}/projects/{project_id}/chat",
    )
    for existing in qdrant.get_collections().collections:
        if existing.name == collection or existing.name.startswith(
            f"rag_{project_id.replace('-', '')}_"
        ):
            qdrant.delete_collection(existing.name)
    qdrant.close()


def upload(rag, content=None, filename=CONTROL.name):
    response = rag.client.post(
        f"{BASE}/upload/single",
        params={"project_id": rag.project_id},
        files={
            "file": (
                filename,
                CONTROL.read_bytes() if content is None else content,
                "text/plain",
            )
        },
    )
    if response.status_code == 200 and response.json().get("collection_name"):
        rag.collection = response.json()["collection_name"]
    return response


def test_upload_retrieve_generate_and_save(rag):
    uploaded = upload(rag)
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "completed"
    assert uploaded.json()["chunks_count"] == 1
    assert rag.qdrant.count(rag.collection, exact=True).count == 1

    # Distractor ensures retrieval chooses the relevant fragment, not just any point.
    asyncio.run(
        rag.store.upsert(
            rag.collection,
            [
                Document(
                    text="Часы работы столовой: 09:00–18:00.",
                    embedding=[0.0, 1.0, 0.0],
                    metadata={"source": "canteen.txt"},
                )
            ],
        )
    )
    response = rag.client.post(
        rag.chat_url,
        json={"question": QUESTION, "score_threshold": 0.9},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["retrieval_status"] == "success"
    assert result["answer"] == ANSWER
    assert result["sources"] == [CONTROL.name]
    messages = rag.completion.call_args.kwargs["messages"]
    assert ANSWER in messages[0]["content"]
    assert "столовой" not in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": QUESTION}
    history = rag.client.get(f"{rag.chat_url}/{result['session_id']}").json()
    assert [(m["role"], m["content"]) for m in history["messages"]] == [
        ("user", QUESTION),
        ("assistant", ANSWER),
    ]
    metadata = history["messages"][-1]["rag_metadata"]
    assert metadata["retrieval_status"] == "success"
    assert metadata["applied_settings"] == result["applied_settings"]
    assert (
        metadata["retrieved_chunks"][0]["metadata"]["index_settings"]["chunk_size"]
        == 800
    )
    assert metadata["retrieved_chunks"][0]["metadata"]["embedding_dimension"] == 3


@pytest.mark.parametrize("top_k", [1, 2])
def test_top_k_changes_real_search_results(rag, top_k):
    assert upload(rag).json()["status"] == "completed"
    asyncio.run(
        rag.store.upsert(
            rag.collection,
            [
                Document(
                    text="Дополнительный контекст.",
                    embedding=[0.8, 0.6, 0.0],
                    metadata={"source": "second.txt"},
                )
            ],
        )
    )
    response = rag.client.patch(
        f"{BASE}/projects/{rag.project_id}",
        json={"top_k": top_k},
    )
    assert response.status_code == 200
    result = rag.client.post(rag.chat_url, json={"question": QUESTION}).json()
    assert result["applied_settings"]["top_k"] == top_k
    assert len(result["sources"]) == top_k
    system = rag.completion.call_args.kwargs["messages"][0]["content"]
    assert ("Дополнительный контекст." in system) == (top_k == 2)


def test_sources_include_only_chunks_actually_passed_to_llm(rag):
    assert upload(rag).json()["status"] == "completed"
    asyncio.run(
        rag.store.upsert(
            rag.collection,
            [
                Document(
                    text="Другой документ.",
                    embedding=[0.8, 0.6, 0.0],
                    metadata={"source": "other.txt"},
                )
            ],
        )
    )
    result = rag.client.post(
        rag.chat_url,
        json={
            "question": QUESTION,
            "top_k": 2,
            "max_context_chars": 3,
        },
    ).json()
    assert result["context_chars"] == 3
    assert result["sources"] == [CONTROL.name]
    history = rag.client.get(f"{rag.chat_url}/{result['session_id']}").json()
    metadata = history["messages"][-1]["rag_metadata"]
    assert len(metadata["retrieved_chunks"]) == 2
    assert len(metadata["context_chunk_ids"]) == 1
    assert metadata["context"] == CONTROL.read_text(encoding="utf-8")[:3]


@pytest.mark.parametrize("limit", [37, 2500])
def test_context_limit_reaches_llm_and_snapshot_survives_setting_changes(rag, limit):
    project_url = f"{BASE}/projects/{rag.project_id}"
    assert rag.client.patch(project_url, json={"chunk_size": 4000}).status_code == 200
    assert upload(rag, b"x" * 3000, "long.txt").json()["status"] == "completed"
    assert (
        rag.client.patch(project_url, json={"max_context_chars": limit}).status_code
        == 200
    )
    result = rag.client.post(rag.chat_url, json={"question": QUESTION}).json()
    assert result["context_chars"] == limit
    assert result["applied_settings"]["max_context_chars"] == limit
    assert rag.completion.call_args.kwargs["messages"][0]["content"].endswith(
        "Контекст:\n" + "x" * limit
    )

    # Per-question overrides must affect execution without changing project defaults.
    overridden = rag.client.post(
        rag.chat_url,
        json={
            "question": QUESTION,
            "top_k": 1,
            "max_context_chars": 17,
        },
    ).json()
    assert overridden["context_chars"] == 17
    assert overridden["applied_settings"]["top_k"] == 1
    assert rag.client.get(project_url).json()["max_context_chars"] == limit
    assert (
        rag.client.patch(project_url, json={"max_context_chars": 100}).status_code
        == 200
    )
    history = rag.client.get(f"{rag.chat_url}/{result['session_id']}").json()
    metadata = history["messages"][-1]["rag_metadata"]
    assert metadata["context"] == "x" * limit
    assert metadata["applied_settings"]["max_context_chars"] == limit
    assert "api_key" not in str(metadata)


@pytest.mark.parametrize(
    "payload",
    [
        {"top_k": 0},
        {"max_context_chars": 0},
        {"temperature": 0.7},
        {"split_by": "tokens"},
    ],
)
def test_chat_rejects_unsupported_or_invalid_overrides(rag, payload):
    response = rag.client.post(rag.chat_url, json={"question": QUESTION, **payload})
    assert response.status_code == 422
    rag.embedder.embed.assert_not_called()
    rag.completion.assert_not_called()


def test_legacy_ineffective_settings_block_chat_and_upload(rag):
    from uuid import UUID
    from backend.db.models import Project
    from backend.tests.conftest import TestingSessionLocal

    with TestingSessionLocal() as db:
        project = db.get(Project, UUID(rag.project_id))
        project.split_by = "tokens"
        db.commit()
    assert rag.client.post(rag.chat_url, json={"question": QUESTION}).status_code == 422
    assert upload(rag).status_code == 422
    rag.embedder.embed.assert_not_called()
    rag.completion.assert_not_called()


@pytest.mark.parametrize("empty_collection", [False, True])
def test_no_context_is_successful_search(rag, empty_collection):
    if not empty_collection:
        assert upload(rag).json()["status"] == "completed"
        rag.embedder.embed.return_value = [[0.0, 1.0, 0.0]]
    result = rag.client.post(
        rag.chat_url,
        json={"question": QUESTION, "score_threshold": 0.9},
    )
    assert result.status_code == 200
    assert result.json()["retrieval_status"] == "no_context"
    assert result.json()["sources"] == []
    assert ANSWER not in rag.completion.call_args.kwargs["messages"][0]["content"]


@pytest.mark.parametrize(
    "failure",
    [
        "unavailable",
        "query_failure",
        "missing_collection",
        "dimension",
        "collection_dimension",
    ],
)
def test_retrieval_failure_never_generates_or_saves(rag, monkeypatch, failure):
    from backend.db.models import ChatHistory
    from backend.tests.conftest import TestingSessionLocal

    if failure == "unavailable":
        monkeypatch.setattr(
            rag.qdrant,
            "get_collection",
            MagicMock(side_effect=ConnectionError("Qdrant unavailable")),
        )
    elif failure == "query_failure":
        asyncio.run(rag.store.create_collection(rag.collection, 3))
        monkeypatch.setattr(
            rag.qdrant,
            "query_points",
            MagicMock(side_effect=ConnectionError("Qdrant query failed")),
        )
    elif failure == "dimension":
        rag.embedder.embed.return_value = [[1.0, 0.0]]
    elif failure == "missing_collection":
        assert upload(rag).json()["status"] == "completed"
        rag.qdrant.delete_collection(rag.collection)
    elif failure == "collection_dimension":
        assert upload(rag).json()["status"] == "completed"
        rag.qdrant.delete_collection(rag.collection)
        asyncio.run(rag.store.create_collection(rag.collection, 2))
    response = rag.client.post(rag.chat_url, json={"question": QUESTION})
    assert response.status_code == 502
    assert response.json()["detail"]["retrieval_status"] == "error"
    assert response.json()["detail"]["status"] == "error"
    assert response.json()["detail"]["applied_settings"]["top_k"] == 5
    rag.completion.assert_not_called()
    with TestingSessionLocal() as db:
        assert db.query(ChatHistory).count() == 0


def test_upload_dimension_mismatch_is_failed(rag):
    rag.embedder.embed.return_value = [[1.0, 0.0]]
    response = upload(rag)
    assert response.json()["status"] == "failed"
    assert not rag.qdrant.collection_exists(rag.collection)


def test_existing_collection_dimension_is_checked(rag):
    assert upload(rag).json()["status"] == "completed"
    rag.qdrant.delete_collection(rag.collection)
    asyncio.run(rag.store.create_collection(rag.collection, 2))
    assert upload(rag).json()["status"] == "failed"
    assert rag.qdrant.count(rag.collection, exact=True).count == 0


@pytest.mark.parametrize("embeddings", [[], [[1.0]], [[1.0] * 3] * 2])
def test_embedding_batch_is_validated(embeddings):
    chunks = [Document(text="control")]
    with pytest.raises(ValueError):
        assign_embeddings(chunks, embeddings, 3)
    assert chunks[0].embedding is None


@pytest.mark.parametrize("embedding", [None, [1.0, 0.0]])
def test_upsert_rejects_missing_or_wrong_dimension(rag, embedding):
    asyncio.run(rag.store.create_collection(rag.collection, 3))
    with pytest.raises(ValueError):
        asyncio.run(
            rag.store.upsert(
                rag.collection,
                [
                    Document(text="control", embedding=embedding),
                ],
            )
        )
    assert rag.qdrant.count(rag.collection, exact=True).count == 0
