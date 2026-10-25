"""Document, configuration and index provenance through the public RAG path."""

import hashlib
import io
from pathlib import Path
from uuid import UUID
import zipfile

import pytest

from backend.db.models import (
    ConfigurationVersion,
    DataSource,
    DocumentVersion,
    ExecutionRun,
    IndexVersion,
)
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
upload = pipeline.upload


@pytest.fixture
def versioned(rag):
    rag.dimension = 3

    async def embeddings(texts):
        return [[1.0] + [0.0] * (rag.dimension - 1) for _ in texts]

    rag.embedder.embed.side_effect = embeddings
    return rag


def question(rag, **overrides):
    response = rag.client.post(
        rag.chat_url, json={"question": pipeline.QUESTION, **overrides}
    )
    assert response.status_code == 200, response.text
    return response.json()


def patch_project(rag, **changes):
    response = rag.client.patch(rag.chat_url.removesuffix("/chat"), json=changes)
    assert response.status_code == 200, response.text


def get(model, key):
    with TestingSessionLocal() as db:
        return db.get(model, UUID(key))


def test_same_name_new_bytes_preserves_old_document_index_and_run(versioned):
    rag = versioned
    first_bytes = "Версия один. Код АЛЬФА-1234.".encode()
    second_bytes = "Версия два. Код БЕТА-5678.".encode()
    first = upload(rag, first_bytes, "control.txt").json()
    before = question(rag)
    old_run = get(ExecutionRun, before["run_id"])
    old_config = get(ConfigurationVersion, before["configuration_version_id"])
    second = upload(rag, second_bytes, "control.txt").json()
    after = question(rag)
    assert first["index_version_id"] != second["index_version_id"]
    assert first["document_version_id"] != second["document_version_id"]
    assert first["documents"][0]["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert second["documents"][0]["sha256"] == hashlib.sha256(second_bytes).hexdigest()
    assert (
        Path(get(DocumentVersion, first["document_version_id"]).file_path).read_bytes()
        == first_bytes
    )
    assert (
        Path(get(DocumentVersion, second["document_version_id"]).file_path).read_bytes()
        == second_bytes
    )
    assert (
        old_run.applied_settings == get(ExecutionRun, before["run_id"]).applied_settings
    )
    assert (
        old_config.settings
        == get(ConfigurationVersion, before["configuration_version_id"]).settings
    )
    assert after["applied_settings"]["documents"] == second["documents"]
    assert rag.qdrant.count(first["collection_name"], exact=True).count == 1
    assert rag.qdrant.count(second["collection_name"], exact=True).count == 1
    with TestingSessionLocal() as db:
        assert db.query(DocumentVersion).count() == 2
        assert db.query(DataSource).filter_by(is_active=True).count() == 1


def test_identical_upload_and_configurations_are_reused(versioned):
    rag = versioned
    first = upload(rag).json()
    calls = rag.embedder.embed.await_count
    second = upload(rag).json()
    assert second["index_reused"] is True
    assert second["index_version_id"] == first["index_version_id"]
    assert second["document_version_id"] == first["document_version_id"]
    assert rag.embedder.embed.await_count == calls
    a, b = question(rag), question(rag)
    assert a["run_id"] != b["run_id"]
    assert a["configuration_version_id"] == b["configuration_version_id"]
    assert get(ExecutionRun, a["run_id"]).index_version_id == UUID(
        first["index_version_id"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"top_k": 2},
        {"max_context_chars": 8000},
        {"system_prompt": "Новый промпт"},
        {"llm_model": "other-llm"},
    ],
)
def test_generation_and_retrieval_options_reuse_index_without_reembedding(
    versioned, changes
):
    rag = versioned
    original = upload(rag).json()
    before = question(rag)
    calls = rag.embedder.embed.await_count
    patch_project(rag, **changes)
    after = question(rag)
    assert after["applied_settings"]["index_version_id"] == original["index_version_id"]
    assert after["configuration_version_id"] != before["configuration_version_id"]
    assert rag.embedder.embed.await_count == calls + 1  # only the new question
    assert get(ExecutionRun, after["run_id"]).details["index_reused"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"chunk_size": 200},
        {"chunk_overlap": 50},
        {"embedding_model": "other-embedding"},
        {"embedding_dimension": 4},
        {"embedding_api_url": "https://other.test/v1"},
    ],
)
def test_incompatible_index_settings_create_separate_collections(versioned, changes):
    rag = versioned
    original = upload(rag).json()
    old_count = rag.qdrant.count(original["collection_name"], exact=True).count
    patch_project(rag, **changes)
    rag.dimension = changes.get("embedding_dimension", 3)
    result = question(rag)
    applied = result["applied_settings"]
    assert applied["index_version_id"] != original["index_version_id"]
    assert applied["collection_name"] != original["collection_name"]
    assert applied["documents_sha256"] == original["documents_sha256"]
    assert rag.qdrant.count(original["collection_name"], exact=True).count == old_count
    assert get(ExecutionRun, result["run_id"]).details["index_reused"] is False


def test_chunking_variants_have_different_chunks_and_switch_back_reuses_original(
    versioned,
):
    rag = versioned
    content = ("alpha beta gamma delta epsilon. " * 90).encode()
    first = upload(rag, content).json()
    patch_project(rag, chunk_size=200, chunk_overlap=30)
    changed = question(rag)
    new_index = get(IndexVersion, changed["applied_settings"]["index_version_id"])
    assert (
        new_index.chunks_count
        > get(IndexVersion, first["index_version_id"]).chunks_count
    )
    points, _ = rag.qdrant.scroll(
        new_index.collection_name, limit=100, with_payload=True
    )
    assert {
        point.payload["metadata"]["index_settings"]["chunk_size"] for point in points
    } == {200}
    patch_project(rag, chunk_size=800, chunk_overlap=100)
    calls = rag.embedder.embed.await_count
    restored = question(rag)
    assert restored["applied_settings"]["index_version_id"] == first["index_version_id"]
    assert rag.embedder.embed.await_count == calls + 1


def test_add_remove_and_restore_documents_freezes_each_corpus(versioned):
    rag = versioned
    first = upload(rag).json()
    second = upload(rag, b"Second document.", "second.txt").json()
    assert len(second["documents"]) == 2
    assert len(get(IndexVersion, first["index_version_id"]).documents) == 1
    assert (
        rag.client.delete(
            f"{pipeline.BASE}/data-sources/{second['data_source_id']}"
        ).status_code
        == 204
    )
    result = question(rag)
    assert result["applied_settings"]["index_version_id"] == first["index_version_id"]
    assert len(get(IndexVersion, second["index_version_id"]).documents) == 2
    calls = rag.embedder.embed.await_count
    restored = upload(rag, b"Second document.", "second.txt").json()
    assert restored["index_reused"] is True
    assert restored["index_version_id"] == second["index_version_id"]
    assert rag.embedder.embed.await_count == calls


def test_failed_new_index_never_falls_back_to_old_index(versioned):
    rag = versioned
    first = upload(rag).json()
    patch_project(rag, embedding_dimension=4)
    # Provider still emits 3-dimensional vectors; rebuild must fail.
    response = rag.client.post(rag.chat_url, json={"question": pipeline.QUESTION})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["applied_settings"]["index_version_id"] != first["index_version_id"]
    assert (
        get(IndexVersion, detail["applied_settings"]["index_version_id"]).status
        == "failed"
    )
    rag.completion.assert_not_called()
    assert rag.qdrant.count(first["collection_name"], exact=True).count == 1


def test_saved_document_checksum_is_verified_on_rebuild(versioned):
    rag = versioned
    first = upload(rag).json()
    path = Path(get(DocumentVersion, first["document_version_id"]).file_path)
    path.write_bytes(b"changed outside the application")
    patch_project(rag, chunk_overlap=50)
    response = rag.client.post(rag.chat_url, json={"question": pipeline.QUESTION})
    assert response.status_code == 502
    rag.completion.assert_not_called()


@pytest.mark.parametrize("kind", ["document", "index", "configuration"])
def test_saved_version_definitions_cannot_be_updated(versioned, kind):
    first = upload(versioned).json()
    result = question(versioned)
    with TestingSessionLocal() as db:
        if kind == "document":
            version = db.get(DocumentVersion, UUID(first["document_version_id"]))
            version.sha256 = "0" * 64
        elif kind == "index":
            version = db.get(IndexVersion, UUID(first["index_version_id"]))
            version.settings = {**version.settings, "chunk_size": 999}
        else:
            version = db.get(
                ConfigurationVersion, UUID(result["configuration_version_id"])
            )
            version.settings = {"changed": True}
        with pytest.raises(ValueError):
            db.commit()
        db.rollback()


def test_zip_uses_the_same_document_manifest_and_index_reuse(versioned, monkeypatch):
    rag = versioned
    monkeypatch.setattr("backend.api.upload.SessionLocal", TestingSessionLocal)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("one.txt", "One control document.")
        archive.writestr("folder/two.txt", "Two control documents.")

    def archive_upload():
        response = rag.client.post(
            f"{pipeline.BASE}/upload/archive",
            params={"project_id": rag.project_id},
            files={"file": ("control.zip", buffer.getvalue(), "application/zip")},
        )
        assert response.status_code == 202
        return rag.client.get(
            f"{pipeline.BASE}/upload/status/{response.json()['operation_id']}"
        ).json()

    first = archive_upload()
    assert first["status"] == "completed", first
    assert len(first["result"]["documents"]) == 2
    calls = rag.embedder.embed.await_count
    second = archive_upload()
    assert second["result"]["index_reused"] is True
    assert rag.embedder.embed.await_count == calls
    assert (
        question(rag)["applied_settings"]["index_version_id"]
        == first["result"]["index_version_id"]
    )


def test_worker_reuses_the_versioned_index(versioned, monkeypatch):
    from backend.tasks.ingestion_tasks import process_document_task

    rag = versioned
    first = upload(rag).json()
    monkeypatch.setattr(
        "backend.tasks.ingestion_tasks.SessionLocal", TestingSessionLocal
    )
    monkeypatch.setattr(
        "backend.tasks.ingestion_tasks.build_embedder", lambda *a, **kw: rag.embedder
    )
    monkeypatch.setattr(
        "backend.tasks.ingestion_tasks.build_vector_store", lambda: rag.store
    )
    source = get(DataSource, first["data_source_id"])
    calls = rag.embedder.embed.await_count
    result = process_document_task.run(
        rag.project_id,
        first["data_source_id"],
        source.file_path,
        800,
        100,
        "control",
        3,
    )
    assert result["index_reused"] is True
    assert result["index_version_id"] == first["index_version_id"]
    assert rag.embedder.embed.await_count == calls


def test_retry_of_partial_index_does_not_duplicate_vectors(versioned, monkeypatch):
    rag = versioned
    content = ("control words repeated for chunking. " * 60).encode()
    original_upsert = rag.store.upsert

    async def fail_partway(collection, chunks):
        await original_upsert(collection, chunks[:1])
        raise ConnectionError("partial write")

    monkeypatch.setattr(rag.store, "upsert", fail_partway)
    failed = upload(rag, content).json()
    assert failed["status"] == "failed"
    assert get(IndexVersion, failed["index_version_id"]).status == "failed"
    monkeypatch.setattr(rag.store, "upsert", original_upsert)
    completed = upload(rag, content).json()
    assert completed["status"] == "completed"
    assert completed["index_version_id"] == failed["index_version_id"]
    count = get(IndexVersion, completed["index_version_id"]).chunks_count
    assert rag.qdrant.count(completed["collection_name"], exact=True).count == count


def test_legacy_sources_require_reupload_without_fabricating_provenance(
    versioned, tmp_path
):
    rag = versioned
    legacy_path = tmp_path / "legacy.txt"
    legacy_path.write_bytes(b"legacy document")
    with TestingSessionLocal() as db:
        db.add(
            DataSource(
                project_id=UUID(rag.project_id),
                file_name="legacy.txt",
                file_path=str(legacy_path),
                status="completed",
            )
        )
        db.commit()
    failed = rag.client.post(rag.chat_url, json={"question": pipeline.QUESTION})
    assert failed.status_code == 502
    assert (
        failed.json()["detail"]["applied_settings"]["index_resolution"] == "unresolved"
    )
    assert upload(rag, b"legacy document", "legacy.txt").json()["status"] == "completed"
    assert question(rag)["retrieval_status"] == "success"


def test_stale_worker_cannot_restore_superseded_document(versioned, monkeypatch):
    from backend.tasks.ingestion_tasks import process_document_task

    rag = versioned
    first = upload(rag, b"original", "same.txt").json()
    old_path = get(DocumentVersion, first["document_version_id"]).file_path
    latest = upload(rag, b"replacement", "same.txt").json()
    monkeypatch.setattr(
        "backend.tasks.ingestion_tasks.SessionLocal", TestingSessionLocal
    )
    result = process_document_task.run(
        rag.project_id, first["data_source_id"], old_path, 800, 100, "control", 3
    )
    assert result["status"] == "superseded"
    assert (
        str(get(DataSource, latest["data_source_id"]).document_version_id)
        == latest["document_version_id"]
    )


def test_simultaneous_requests_build_one_index_without_duplicate_embeddings(versioned):
    import asyncio
    from unittest.mock import AsyncMock
    from backend.core.versioning import ensure_index, plan_index
    from backend.db.models import Project

    rag = versioned
    assert upload(rag).json()["status"] == "completed"
    patch_project(rag, chunk_overlap=50)
    with TestingSessionLocal() as first_db, TestingSessionLocal() as second_db:
        index = plan_index(first_db, first_db.get(Project, UUID(rag.project_id)))
        first_db.commit()
        other = second_db.get(IndexVersion, index.id)

        async def embeddings(texts):
            await asyncio.sleep(0)
            return [[1.0, 0.0, 0.0] for _ in texts]

        embedder = type("Embedder", (), {"embed": AsyncMock(side_effect=embeddings)})()

        async def build_both():
            return await asyncio.gather(
                ensure_index(first_db, index, rag.store, embedder),
                ensure_index(second_db, other, rag.store, embedder),
            )

        assert sorted(asyncio.run(build_both())) == [False, True]
        assert embedder.embed.await_count == 1
