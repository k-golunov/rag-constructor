"""Raw provider errors must stay out of public status responses and stored traces."""

import io
import zipfile
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.api import upload as upload_api
from backend.core.ingestion.parser import EmptyPDFError, ParserError
from backend.core.public_errors import (
    DOCUMENT_PROCESSING_ERROR,
    ARCHIVE_PROCESSING_ERROR,
)
from backend.db.models import DataSource, ExecutionRun
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
SECRET = "opaque-provider-credential-without-recognizable-prefix"


def test_single_upload_error_is_neutral_in_db_and_public_sources(
    rag, monkeypatch, caplog
):
    monkeypatch.setattr(
        rag.embedder, "embed", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    response = pipeline.upload(rag)
    assert response.json()["status"] == "failed"
    sources = rag.client.get(f"{pipeline.BASE}/projects/{rag.project_id}/data-sources")
    assert sources.json()["items"][0]["error_message"] == DOCUMENT_PROCESSING_ERROR
    assert SECRET not in response.text + sources.text
    with TestingSessionLocal() as db:
        source = db.get(DataSource, UUID(response.json()["data_source_id"]))
        assert source.error_message == DOCUMENT_PROCESSING_ERROR
        source.error_message = SECRET  # Historical errors also must not be exposed.
        db.commit()
    assert (
        SECRET
        not in rag.client.get(
            f"{pipeline.BASE}/projects/{rag.project_id}/data-sources"
        ).text
    )
    assert SECRET in caplog.text


def test_archive_failure_polling_is_neutral_including_legacy_status(
    rag, monkeypatch, caplog
):
    monkeypatch.setattr(upload_api, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        rag.embedder, "embed", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("control.txt", "Archive control text")
    response = rag.client.post(
        f"{pipeline.BASE}/upload/archive",
        params={"project_id": rag.project_id},
        files={"file": ("control.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    status = rag.client.get(f"{pipeline.BASE}/upload/status/{operation_id}")
    assert (
        status.json()["error"] == ARCHIVE_PROCESSING_ERROR and SECRET not in status.text
    )
    assert upload_api._OPERATIONS[operation_id]["error"] == ARCHIVE_PROCESSING_ERROR
    upload_api._OPERATIONS[operation_id]["error"] = SECRET
    assert (
        SECRET
        not in rag.client.get(f"{pipeline.BASE}/upload/status/{operation_id}").text
    )
    upload_api._OPERATIONS.pop(operation_id)
    assert SECRET in caplog.text


@pytest.mark.parametrize(
    "error,code", [(ParserError(SECRET), 400), (EmptyPDFError(SECRET), 422)]
)
def test_parser_errors_never_echo_exception_text(rag, monkeypatch, error, code):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(upload_api, "process_file", fail)
    response = pipeline.upload(rag)
    assert response.status_code == code and SECRET not in response.text


def test_publicly_exported_trace_does_not_contain_unknown_exception_secret(
    rag, monkeypatch, caplog
):
    assert pipeline.upload(rag).json()["status"] == "completed"
    monkeypatch.setattr(
        rag.store, "search", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    response = rag.client.post(rag.chat_url, json={"question": pipeline.QUESTION})
    assert response.status_code == 502 and SECRET not in response.text
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(response.json()["detail"]["run_id"]))
        assert SECRET not in str(run.response_trace)
        assert run.response_trace["errors"][0]["type"] == "RuntimeError"
    assert SECRET in caplog.text


def test_legacy_archive_handler_never_stores_exception_text(monkeypatch, tmp_path):
    from backend.core.ingestion import pipeline as ingestion

    directory = tmp_path / "legacy"
    directory.mkdir()

    def fail(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ingestion.zipfile, "ZipFile", fail)
    operations = {}
    ingestion.process_archive(
        tmp_path / "control.zip", directory, 800, 100, operations, "legacy"
    )
    assert operations["legacy"]["error"] == ARCHIVE_PROCESSING_ERROR


def test_celery_ingestion_retry_uses_neutral_error(rag, monkeypatch):
    from unittest.mock import Mock
    from backend.tasks import ingestion_tasks as tasks

    response = pipeline.upload(rag).json()
    with TestingSessionLocal() as db:
        source = db.get(DataSource, UUID(response["data_source_id"]))
        source_id, file_path = str(source.id), source.file_path
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(tasks, "build_embedder", lambda *args, **kwargs: rag.embedder)
    monkeypatch.setattr(tasks, "build_vector_store", lambda: rag.store)
    monkeypatch.setattr(
        tasks, "ensure_index", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    retry = Mock(side_effect=RuntimeError("retry scheduled"))
    monkeypatch.setattr(tasks.process_document_task, "retry", retry)
    with pytest.raises(RuntimeError, match="retry scheduled"):
        tasks.process_document_task.run(
            rag.project_id, source_id, file_path, 800, 100, "control", 3
        )
    assert str(retry.call_args.kwargs["exc"]) == DOCUMENT_PROCESSING_ERROR
    with TestingSessionLocal() as db:
        assert (
            db.get(DataSource, UUID(source_id)).error_message
            == DOCUMENT_PROCESSING_ERROR
        )
