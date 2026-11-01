"""Real local Qdrant → common executor → pinned batch results."""

import asyncio
from uuid import UUID, uuid4
from unittest.mock import AsyncMock
import pytest

from backend.db.models import (
    BatchRun,
    BatchVariant,
    BatchQuestionResult,
    BatchResultAttempt,
    ChatHistory,
    ExecutionRun,
    BatchJob,
)
from backend.core.batch import execution_lock
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_question_sets as sets
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
qs = sets.qs


@pytest.fixture(autouse=True)
def no_external_broker(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(
        "backend.api.batches.publish_job", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(settings, "BATCH_JOB_MAX_ATTEMPTS", 1)


def drain(qs, batch):
    from backend.core.batch_queue import perform_job
    from backend.api import chat

    for _ in range(100):
        with TestingSessionLocal() as db:
            record = db.get(BatchRun, UUID(batch["id"]))
            job = (
                db.get(BatchJob, record.active_job_id) if record.active_job_id else None
            )
            if job is None or job.status not in {"queued", "published", "running"}:
                break
            asyncio.run(
                perform_job(
                    db,
                    job.id,
                    embedder_factory=chat.build_embedder,
                    store_factory=chat.build_vector_store,
                    generate=chat._generate_traced,
                )
            )
    return qs.client.get(f"{qs.batches}/{batch['id']}").json()


def reviewed(qs):
    draft = sets.create(
        qs,
        [
            {
                "question": pipeline.QUESTION,
                "reference_answer": "private-reference",
                "split": "test",
            },
            {"question": "Когда открыт архив?", "unanswerable": True, "split": "test"},
        ],
    )
    for example in draft["examples"]:
        draft = sets.approve(qs, draft, example["id"])
    return draft


def make(qs, version, **extra):
    qs.batches = f"{pipeline.BASE}/projects/{qs.project_id}/batches"
    response = qs.client.post(
        qs.batches,
        json={
            "name": "Control batch",
            "question_set_version_id": version["id"],
            "split": "test",
            "variants": [
                {"name": "one", "settings": {"top_k": 1}},
                {"name": "two", "settings": {"top_k": 2, "chunk_size": 400}},
            ],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def run(qs, batch):
    response = qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    assert response.status_code == 202, response.text
    return drain(qs, batch)


def answers(qs, batch):
    response = qs.client.get(f"{qs.batches}/{batch['id']}/results?limit=100")
    assert response.status_code == 200
    return response.json()["items"]


def test_multiple_variants_repeats_pin_versions_and_isolate_questions(qs):
    version = reviewed(qs)
    batch = make(qs, version, repeats=2)
    variant_ids = [v["configuration_version_id"] for v in batch["variants"]]
    indexes = [v["index_version_id"] for v in batch["variants"]]
    assert len(set(indexes)) == 2
    # Later project/corpus edits cannot change prepared settings or dataset documents.
    qs.client.patch(
        f"{pipeline.BASE}/projects/{qs.project_id}",
        json={"top_k": 30, "llm_model": "changed"},
    )
    pipeline.upload(qs, b"New unrelated document", filename="new.txt")
    qs.completion.reset_mock()
    done = run(qs, batch)
    assert done["status"] == "completed"
    assert done["counts"] == {"pending": 0, "running": 0, "success": 8, "error": 0}
    items = answers(qs, done)
    for result in items:
        attempt = result["attempts"][0]
        assert attempt["configuration_version_id"] in variant_ids
        assert attempt["index_version_id"] in indexes
        assert attempt["response_trace"]["history"] == []
        assert len(attempt["response_trace"]["messages"]) == 2
        assert "private-reference" not in str(attempt["response_trace"]["messages"])
        assert "test-key" not in str(result)
        assert attempt["response_trace"]["answer"] == pipeline.ANSWER
        assert attempt["response_trace"]["retrieved_chunks"]
    assert len(qs.completion.call_args_list) == 8
    assert all(
        call.kwargs["model"] == "control" for call in qs.completion.call_args_list
    )
    with TestingSessionLocal() as db:
        assert db.query(ChatHistory).count() == 0
        assert {str(r.question_set_version_id) for r in db.query(ExecutionRun)} == {
            version["id"]
        }
    assert run(qs, batch)["counts"]["success"] == 8
    assert qs.completion.call_count == 8  # no accidental rerun on execute
    clone = qs.client.post(f"{qs.batches}/{batch['id']}/rerun").json()
    assert clone["rerun_of_id"] == batch["id"]
    assert clone["counts"]["pending"] == 8
    assert [v["configuration_version_id"] for v in clone["variants"]] == variant_ids
    assert run(qs, clone)["counts"]["success"] == 8
    assert {
        x["attempts"][0]["execution_run_id"] for x in answers(qs, clone)
    }.isdisjoint({x["attempts"][0]["execution_run_id"] for x in items})


def test_qdrant_failure_continues_batch_and_retry_preserves_first_error(
    qs, monkeypatch
):
    batch = make(qs, reviewed(qs))
    original = qs.store.search
    monkeypatch.setattr(
        qs.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    qs.completion.reset_mock()
    failed = run(qs, batch)
    assert failed["status"] == "completed_with_errors"
    assert failed["counts"]["error"] == 4
    assert qs.completion.call_count == 0
    items = answers(qs, batch)
    first = items[0]
    assert first["attempts"][0]["error_code"] == "retrieval_error"
    monkeypatch.setattr(qs.store, "search", original)
    response = qs.client.post(f"{qs.batches}/{batch['id']}/results/{first['id']}/retry")
    assert response.status_code == 202, response.text
    drain(qs, batch)
    updated = answers(qs, batch)[0]
    assert updated["status"] == "success"
    assert [a["kind"] for a in updated["attempts"]] == ["initial", "technical_retry"]
    assert [a["status"] for a in updated["attempts"]] == ["error", "success"]
    assert (
        qs.client.post(
            f"{qs.batches}/{batch['id']}/results/{first['id']}/retry"
        ).status_code
        == 409
    )


def test_search_stage_retry_is_inside_result_not_new_repeat(qs, monkeypatch):
    original = qs.store.search
    count = 0

    async def unstable(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            raise ConnectionError("temporary")
        return await original(*args, **kwargs)

    monkeypatch.setattr(qs.store, "search", unstable)
    batch = make(
        qs, reviewed(qs), variants=[{"name": "retry", "settings": {"max_attempts": 2}}]
    )
    assert run(qs, batch)["counts"]["success"] == 2
    result = answers(qs, batch)[0]
    assert result["repeat_number"] == 1 and len(result["attempts"]) == 1
    trace = result["attempts"][0]["response_trace"]
    search = [a for a in trace["attempts"] if a["stage"] == "search"]
    assert [a["status"] for a in search] == ["error", "success"]


@pytest.mark.parametrize(
    "settings",
    [
        {"temperature": 0.1},
        {"extract_tables": True},
        {"chunk_size": 100, "chunk_overlap": 100},
    ],
)
def test_unsupported_and_invalid_variant_settings_rejected(qs, settings):
    version = reviewed(qs)
    url = f"{pipeline.BASE}/projects/{qs.project_id}/batches"
    response = qs.client.post(
        url,
        json={
            "name": "Invalid",
            "question_set_version_id": version["id"],
            "split": "test",
            "variants": [{"name": "x", "settings": settings}],
        },
    )
    assert response.status_code == 422
    with TestingSessionLocal() as db:
        assert db.query(BatchRun).count() == 0


def test_approval_split_and_project_scope(qs):
    pending = sets.create(qs)
    url = f"{pipeline.BASE}/projects/{qs.project_id}/batches"
    payload = {
        "name": "x",
        "question_set_version_id": pending["id"],
        "split": "test",
        "variants": [{"name": "x"}],
    }
    assert qs.client.post(url, json=payload).status_code == 422
    approved = sets.approve(qs, pending)
    payload["question_set_version_id"] = approved["id"]
    payload["split"] = "tuning"
    assert qs.client.post(url, json=payload).status_code == 422
    batch = make(qs, approved)
    wrong = f"{pipeline.BASE}/projects/{uuid4()}/batches/{batch['id']}"
    assert qs.client.get(wrong).status_code == 404
    assert qs.client.post(f"{wrong}/execute").status_code == 404
    assert qs.client.get(f"{wrong}/results").status_code == 404


def test_generation_only_variants_reuse_index(qs):
    batch = make(
        qs,
        reviewed(qs),
        variants=[
            {"name": "a", "settings": {"top_k": 1}},
            {"name": "b", "settings": {"top_k": 2, "llm_model": "other"}},
        ],
    )
    assert len({v["index_version_id"] for v in batch["variants"]}) == 1
    assert len({v["configuration_version_id"] for v in batch["variants"]}) == 2


def test_running_batch_locked_and_interrupted_result_recovered(qs):
    batch = make(qs, reviewed(qs))
    with TestingSessionLocal() as db:
        record = db.get(BatchRun, UUID(batch["id"]))
        with execution_lock(db, record):
            assert (
                qs.client.post(f"{qs.batches}/{batch['id']}/execute").status_code == 202
            )
    assert run(qs, batch)["counts"]["success"] == 4
    first = answers(qs, batch)[0]
    with TestingSessionLocal() as db:
        result = db.get(BatchQuestionResult, UUID(first["id"]))
        execution = db.get(ExecutionRun, UUID(first["attempts"][0]["execution_run_id"]))
        result.status = execution.status = "running"
        db.commit()
    assert run(qs, batch)["counts"]["success"] == 4
    recovered = answers(qs, batch)[0]
    assert recovered["attempts"][0]["error_code"] == "execution_interrupted"


def test_batch_and_chat_use_same_rag_settings_and_context(qs):
    version = reviewed(qs)
    batch = make(
        qs,
        version,
        variants=[{"name": "same", "settings": {"top_k": 1}}],
        example_ids=[version["examples"][0]["id"]],
    )
    assert run(qs, batch)["counts"]["success"] == 1
    result = answers(qs, batch)[0]["attempts"][0]
    chat = qs.client.post(
        qs.chat_url, json={"question": pipeline.QUESTION, "top_k": 1}
    ).json()
    assert chat["configuration_version_id"] == result["configuration_version_id"]
    with TestingSessionLocal() as db:
        execution = db.get(ExecutionRun, UUID(chat["run_id"]))
        assert (
            execution.response_trace["messages"] == result["response_trace"]["messages"]
        )
        assert (
            execution.response_trace["context"] == result["response_trace"]["context"]
        )


def test_generation_failure_and_empty_search_are_distinct(qs, monkeypatch):
    batch = make(qs, reviewed(qs))
    qs.completion.side_effect = RuntimeError("provider failed")
    assert run(qs, batch)["counts"]["error"] == 4
    assert {a["error_code"] for r in answers(qs, batch) for a in r["attempts"]} == {
        "generation_error"
    }
    qs.completion.side_effect = None
    monkeypatch.setattr(qs.store, "search", AsyncMock(return_value=[]))
    clone = qs.client.post(f"{qs.batches}/{batch['id']}/rerun").json()
    assert run(qs, clone)["counts"]["success"] == 4
    for result in answers(qs, clone):
        trace = result["attempts"][0]["response_trace"]
        assert trace["retrieval_status"] == "no_context" and trace["context"] == ""


def test_batch_definition_is_immutable(qs):
    batch = make(qs, reviewed(qs))
    with TestingSessionLocal() as db:
        variant = db.get(BatchVariant, UUID(batch["variants"][0]["id"]))
        variant.project_snapshot = {"top_k": 99}
        with pytest.raises(ValueError, match="неизменяема"):
            db.commit()
        db.rollback()
        record = db.get(BatchRun, UUID(batch["id"]))
        record.selection = {"example_ids": []}
        with pytest.raises(ValueError, match="неизменяемо"):
            db.commit()
        db.rollback()


def test_batch_migration_upgrade_and_downgrade():
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from backend.db.database import Base

    migration = importlib.import_module(
        "backend.alembic.versions.20261025_0008_batches"
    )
    engine = create_engine("sqlite://")
    names = {
        "batch_runs",
        "batch_variants",
        "batch_question_results",
        "batch_result_attempts",
    }
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(
            connection,
            tables=[
                table
                for table in Base.metadata.sorted_tables
                if table.name not in names
                and table.name not in {"batch_jobs", "evaluation_checks"}
            ],
        )
        before = set(inspect(connection).get_table_names())
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == before | names
            for name in names:
                assert {c["name"] for c in inspect(connection).get_columns(name)} == (
                    set(Base.metadata.tables[name].columns.keys())
                    - (
                        {"cancel_requested", "active_job_id"}
                        if name == "batch_runs"
                        else set()
                    )
                )
                assert inspect(connection).get_foreign_keys(name)
            migration.downgrade()
        assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_recovery_after_answer_commit_does_not_repeat_success(qs):
    batch = make(qs, reviewed(qs))
    assert run(qs, batch)["counts"]["success"] == 4
    first = answers(qs, batch)[0]
    with TestingSessionLocal() as db:
        result = db.get(BatchQuestionResult, UUID(first["id"]))
        result.status = "running"
        db.commit()
    count = qs.completion.call_count
    assert run(qs, batch)["counts"]["success"] == 4
    assert qs.completion.call_count == count
