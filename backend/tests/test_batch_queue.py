"""Durable dispatch, worker loss, cancellation and duplicate delivery."""

import asyncio
from datetime import timedelta
from uuid import UUID
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from backend.api import chat
from backend.config import settings
from backend.core import batch_queue as queue
from backend.core.batch import execution_lock
from backend.db.models import BatchRun, BatchJob, BatchResultAttempt, ExecutionRun
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_batches as batches
from backend.tests import test_question_sets as sets
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
qs = sets.qs


@pytest.fixture
def prepared(qs, monkeypatch):
    monkeypatch.setattr(
        "backend.api.batches.publish_job", lambda *args, **kwargs: False
    )
    batch = batches.make(qs, batches.reviewed(qs), variants=[{"name": "one"}])
    response = qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    assert response.status_code == 202 and qs.completion.call_count == 0
    return qs, response.json()


def perform(job_id, generate=None):
    with TestingSessionLocal() as db:
        return asyncio.run(
            queue.perform_job(
                db,
                job_id,
                embedder_factory=chat.build_embedder,
                store_factory=chat.build_vector_store,
                generate=generate or chat._generate_traced,
            )
        )


def active(batch):
    with TestingSessionLocal() as db:
        record = db.get(BatchRun, UUID(batch["id"]))
        return record.active_job_id


def ready(job_id):
    with TestingSessionLocal() as db:
        job = db.get(BatchJob, job_id)
        job.available_at = queue.now() - timedelta(seconds=1)
        db.commit()


def test_api_persists_outbox_without_running_and_repeat_submit_is_idempotent(prepared):
    qs, batch = prepared
    first = active(batch)
    response = qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    assert response.status_code == 202 and active(batch) == first
    assert qs.completion.call_count == 0
    assert batch["status"] == "queued"
    assert batch["counts"]["pending"] == 2
    assert perform(first) == "success"
    assert perform(first) == "ignored"
    assert qs.completion.call_count == 1
    assert perform(active(batch)) == "success"
    assert qs.client.get(f"{qs.batches}/{batch['id']}").json()["counts"]["success"] == 2


def test_broker_failure_is_durable_and_publish_window_repaired(prepared):
    _, batch = prepared
    job_id = active(batch)
    failing = Mock(side_effect=ConnectionError("secret must not persist"))
    with TestingSessionLocal() as db:
        job = db.get(BatchJob, job_id)
        assert not queue.publish_job(db, job, failing)
        assert job.status == "queued" and job.error_code == "broker_unavailable"
        assert "secret" not in str(queue.job_info(job))
    ready(job_id)
    sender = Mock()
    with TestingSessionLocal() as db:
        assert queue.dispatch_due(db, sender) == 1
        job = db.get(BatchJob, job_id)
        assert job.status == "published"
        assert queue.dispatch_due(db, sender) == 0
        job.lease_expires_at = queue.now() - timedelta(seconds=1)
        db.commit()
        assert queue.dispatch_due(db, sender) == 1
    assert sender.call_count == 2
    assert sender.call_args.kwargs["args"] == [str(job_id)]
    assert perform(job_id) == "success"
    assert perform(job_id) == "ignored"


def test_cancel_before_delivery_and_resume(prepared):
    qs, batch = prepared
    old = active(batch)
    response = qs.client.post(f"{qs.batches}/{batch['id']}/cancel")
    assert response.status_code == 202 and response.json()["status"] == "cancelled"
    assert perform(old) == "ignored" and qs.completion.call_count == 0
    assert qs.client.post(f"{qs.batches}/{batch['id']}/execute").status_code == 202
    assert active(batch) != old
    assert perform(active(batch)) == "success"


def test_cancel_during_question_keeps_answer_and_does_not_start_next(prepared):
    qs, batch = prepared
    original = chat._generate_traced

    async def cancel_and_generate(*args, **kwargs):
        with TestingSessionLocal() as db:
            queue.cancel(db, db.get(BatchRun, UUID(batch["id"])))
        return await original(*args, **kwargs)

    assert perform(active(batch), cancel_and_generate) == "success"
    status = qs.client.get(f"{qs.batches}/{batch['id']}").json()
    assert status["status"] == "cancelled" and status["counts"]["success"] == 1
    assert status["counts"]["pending"] == 1
    qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    assert perform(active(batch)) == "success"
    assert qs.completion.call_count == 2


def test_worker_loss_preserves_completed_answers_and_retry_lineage(prepared):
    qs, batch = prepared
    assert perform(active(batch)) == "success"
    lost_job = active(batch)

    class WorkerLost(BaseException):
        pass

    async def crash(*args, **kwargs):
        raise WorkerLost()

    with pytest.raises(WorkerLost):
        perform(lost_job, crash)
    assert qs.completion.call_count == 1
    assert perform(lost_job) == "success"
    assert qs.completion.call_count == 2
    results = batches.answers(qs, batch)
    assert len(results[0]["attempts"]) == 1
    assert [a["status"] for a in results[1]["attempts"]] == ["error", "success"]
    assert results[1]["attempts"][0]["error_code"] == "execution_interrupted"


def test_transient_failure_retries_are_bounded_and_do_not_make_new_repeat(
    prepared, monkeypatch
):
    qs, batch = prepared
    job_id = active(batch)
    monkeypatch.setattr(
        qs.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    for number in range(1, 4):
        ready(job_id)
        outcome = perform(job_id)
        assert outcome == ("retry" if number < 3 else "error")
    assert perform(job_id) == "ignored" and qs.completion.call_count == 0
    first = batches.answers(qs, batch)[0]
    assert first["repeat_number"] == 1
    assert [a["kind"] for a in first["attempts"]] == [
        "initial",
        "technical_retry",
        "technical_retry",
    ]


def test_question_timeout_saved_and_retryable(prepared):
    qs, batch = prepared
    job_id = active(batch)
    with TestingSessionLocal() as db:
        job = db.get(BatchJob, job_id)
        job.policy = {**job.policy, "question_timeout_seconds": 0.02}
        db.commit()

    async def stalled(*args, **kwargs):
        await asyncio.sleep(5)

    assert perform(job_id, stalled) == "retry"
    attempt = batches.answers(qs, batch)[0]["attempts"][0]
    assert attempt["error_code"] == "execution_timeout" and attempt["status"] == "error"


def test_overlapping_delivery_does_not_claim_or_duplicate(prepared):
    _, batch = prepared
    job_id = active(batch)
    with TestingSessionLocal() as db:
        record = db.get(BatchRun, UUID(batch["id"]))
        with execution_lock(db, record):
            with pytest.raises(HTTPException) as error:
                perform(job_id)
            assert error.value.status_code == 409
        assert db.get(BatchJob, job_id).execution_attempts == 0


def test_celery_registration_and_delivery_options():
    from backend.tasks.worker import celery_app

    celery_app.loader.import_default_modules()
    task = celery_app.tasks["backend.tasks.batch_tasks.process_batch_job"]
    assert task.acks_late and task.reject_on_worker_lost and task.ignore_result
    assert "backend.tasks.ingestion_tasks.process_document_task" in celery_app.tasks
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.beat_schedule["batch-outbox"]["schedule"] == 5


def test_queue_migration_round_trip():
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from backend.db.database import Base

    migration = importlib.import_module(
        "backend.alembic.versions.20261025_0009_batch_queue"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Base.metadata.create_all(
            connection,
            tables=[
                t
                for t in Base.metadata.sorted_tables
                if t.name not in {"batch_jobs", "evaluation_checks"}
            ],
        )
        with Operations.context(MigrationContext.configure(connection)) as operations:
            # Simulate the original 0008 batch_runs layout.
            with operations.batch_alter_table("batch_runs") as batch_op:
                batch_op.drop_column("cancel_requested")
                batch_op.drop_column("active_job_id")
            migration.upgrade()
            assert set(
                inspect(connection).get_columns("batch_jobs")[i]["name"]
                for i in range(len(inspect(connection).get_columns("batch_jobs")))
            ) == set(Base.metadata.tables["batch_jobs"].columns.keys())
            migration.downgrade()
        assert "batch_jobs" not in inspect(connection).get_table_names()
    engine.dispose()


def test_cancel_backoff_then_resume_retries_same_question(prepared, monkeypatch):
    qs, batch = prepared
    original = qs.store.search
    monkeypatch.setattr(
        qs.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    failed_job = active(batch)
    assert perform(failed_job) == "retry"
    qs.client.post(f"{qs.batches}/{batch['id']}/cancel")
    monkeypatch.setattr(qs.store, "search", original)
    qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    assert perform(active(batch)) == "success"
    first = batches.answers(qs, batch)[0]
    assert [a["status"] for a in first["attempts"]] == ["error", "success"]
    assert first["attempts"][0]["background_job"]["id"] == str(failed_job)
    assert first["attempts"][1]["background_job"]["id"] != str(failed_job)


def test_crash_budget_exhausted_before_rag_marks_result_error_and_advances(prepared):
    qs, batch = prepared
    job_id = active(batch)
    with TestingSessionLocal() as db:
        job = db.get(BatchJob, job_id)
        job.execution_attempts = job.policy["max_attempts"]
        db.commit()
    assert perform(job_id) == "error"
    first = batches.answers(qs, batch)[0]
    assert first["status"] == "error" and first["attempts"] == []
    assert first["jobs"][0]["error_code"] == "question_failed"
    assert active(batch) != job_id
    assert perform(active(batch)) == "success"
    assert qs.completion.call_count == 1
