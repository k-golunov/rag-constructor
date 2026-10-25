"""Pinned batches, isolated RAG questions, and explicit retry lineage."""

import copy
import asyncio
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from weakref import WeakValueDictionary

from fastapi import HTTPException
from sqlalchemy import text, func

from backend.core.execution import (
    configuration_version,
    execution_settings,
    fail_run,
    resolved_api_url,
)
from backend.core.question_sets import check_no_credentials, project_or_404, set_or_404
from backend.core.rag import execute_rag, _execution_secrets
from backend.core.versioning import plan_index, index_snapshot
from backend.db.models import (
    BatchRun,
    BatchJob,
    BatchVariant,
    BatchQuestionResult,
    BatchResultAttempt,
    ExecutionRun,
    IndexVersion,
    QuestionSetVersion,
    ConfigurationVersion,
    EvaluationCheck,
    QuestionSet,
)
from backend.db.rag_schemas import ChatRequest
from backend.db.schemas import ProjectCreate

_locks_guard = threading.Lock()
_locks = WeakValueDictionary()


def batch_or_404(db, project_id, batch_id):
    batch = db.query(BatchRun).filter_by(id=batch_id, project_id=project_id).first()
    if batch is None:
        raise HTTPException(404, "Пакетный запуск не найден.")
    return batch


def variants(db, batch):
    return (
        db.query(BatchVariant)
        .filter_by(batch_run_id=batch.id)
        .order_by(BatchVariant.position)
        .all()
    )


def results(db, variant, status=None, result_id=None):
    query = db.query(BatchQuestionResult).filter_by(variant_id=variant.id)
    if status is not None:
        query = query.filter_by(status=status)
    if result_id is not None:
        query = query.filter_by(id=result_id)
    return query.order_by(
        BatchQuestionResult.repeat_number, BatchQuestionResult.question_position
    ).all()


def prepare_batch(db, project_id, payload):
    project = project_or_404(db, project_id)
    version = db.get(QuestionSetVersion, payload.question_set_version_id)
    if version is None:
        raise HTTPException(404, "Версия набора не найдена.")
    set_or_404(db, project_id, version.question_set_id)
    examples = [e for e in version.examples if e["split"] == payload.split]
    if payload.example_ids is not None:
        requested = {str(value) for value in payload.example_ids}
        if len(requested) != len(payload.example_ids) or not requested.issubset(
            {e["id"] for e in examples}
        ):
            raise HTTPException(
                422,
                "Идентификаторы вопросов повторяются или не принадлежат выбранному разделению.",
            )
        examples = [e for e in examples if e["id"] in requested]
    if not examples or any(e["review"]["status"] != "approved" for e in examples):
        raise HTTPException(
            422, "Выберите непустой набор вопросов, проверенных человеком."
        )
    if len(examples) * len(payload.variants) * payload.repeats > 10000:
        raise HTTPException(422, "Пакет ограничен 10000 результатами.")
    if len({v.name for v in payload.variants}) != len(payload.variants):
        raise HTTPException(422, "Названия вариантов должны быть уникальны.")
    check_no_credentials(payload.name, project)
    plans = [resolve_variant(db, project, variant) for variant in payload.variants]
    return project, version, examples, plans


def resolve_variant(db, project, variant):
    snapshot = {
        name: getattr(project, name)
        for name in ProjectCreate.model_fields
        if name not in {"embedding_api_key", "llm_api_key"}
    }
    inherited = {}
    if variant.configuration_version_id is not None:
        config = db.get(ConfigurationVersion, variant.configuration_version_id)
        if config is None or config.project_id != project.id:
            raise HTTPException(404, "Конфигурация не найдена в проекте.")
        source = (
            db.query(BatchVariant).filter_by(configuration_version_id=config.id).first()
        )
        if source is None or config.settings.get("mode") != "rag":
            raise HTTPException(
                422,
                "Для повторного использования выберите сохранённый вариант сбора ответов.",
            )
        snapshot.update(copy.deepcopy(source.project_snapshot))
        inherited = {
            key: config.settings[key]
            for key in ("top_k", "max_context_chars", "score_threshold", "max_attempts")
        }
    overrides = variant.settings.model_dump(exclude_unset=True)
    overrides = {
        key: value
        for key, value in overrides.items()
        if value is not None or key == "score_threshold"
    }
    snapshot.update({key: value for key, value in overrides.items() if key in snapshot})
    for field in ("embedding_api_url", "llm_api_url"):
        snapshot[field] = resolved_api_url(snapshot[field])
    snapshot = ProjectCreate.model_validate(snapshot).model_dump(
        exclude={"embedding_api_key", "llm_api_key"}
    )
    check_no_credentials(snapshot, project)
    check_no_credentials(variant.name, project)
    frozen = SimpleNamespace(id=project.id, **snapshot)
    inherited.update(
        {
            key: value
            for key, value in overrides.items()
            if key in {"top_k", "max_context_chars", "score_threshold", "max_attempts"}
        }
    )
    options = ChatRequest(question="configuration", **inherited)
    applied = execution_settings(frozen, "rag", options)
    return {
        "name": variant.name,
        "snapshot": snapshot,
        "project": frozen,
        "settings": applied,
    }


def create_batch(db, project_id, payload):
    project, version, examples, plans = prepare_batch(db, project_id, payload)
    if payload.preview_sha256 is not None:
        from backend.core.metrics_analysis import plan_checksum

        if payload.preview_sha256 != plan_checksum(
            version, examples, plans, payload.repeats
        ):
            raise HTTPException(
                409,
                "Настройки изменились после предпросмотра. Проверьте варианты ещё раз.",
            )
    batch = BatchRun(
        project_id=project_id,
        question_set_version_id=version.id,
        name=payload.name,
        repeats=payload.repeats,
        selection={
            "split": payload.split,
            "example_ids": [e["id"] for e in examples],
            "history_policy": "independent",
        },
    )
    db.add(batch)
    db.flush()
    for position, plan in enumerate(plans):
        snapshot, frozen, applied = plan["snapshot"], plan["project"], plan["settings"]
        index = plan_index(db, frozen, documents=copy.deepcopy(version.documents))
        applied.update(index_snapshot(index))
        config = configuration_version(db, frozen, applied, _execution_secrets(project))
        record = BatchVariant(
            batch_run_id=batch.id,
            position=position,
            name=plan["name"],
            project_snapshot=snapshot,
            applied_settings=config.settings,
            configuration_version_id=config.id,
            index_version_id=index.id,
        )
        db.add(record)
        db.flush()
        _add_results(db, batch, record)
    check_no_credentials(batch.name, project)
    db.commit()
    return batch


def _add_results(db, batch, variant):
    for repeat in range(1, batch.repeats + 1):
        for position, example_id in enumerate(batch.selection["example_ids"]):
            db.add(
                BatchQuestionResult(
                    variant_id=variant.id,
                    question_example_id=UUID(example_id),
                    question_position=position,
                    repeat_number=repeat,
                )
            )


def rerun_batch(db, source):
    clone = BatchRun(
        project_id=source.project_id,
        question_set_version_id=source.question_set_version_id,
        name=source.name,
        repeats=source.repeats,
        selection=copy.deepcopy(source.selection),
        rerun_of_id=source.id,
    )
    db.add(clone)
    db.flush()
    for old in variants(db, source):
        new = BatchVariant(
            batch_run_id=clone.id,
            position=old.position,
            name=old.name,
            configuration_version_id=old.configuration_version_id,
            index_version_id=old.index_version_id,
            project_snapshot=copy.deepcopy(old.project_snapshot),
            applied_settings=copy.deepcopy(old.applied_settings),
        )
        db.add(new)
        db.flush()
        _add_results(db, clone, new)
    db.commit()
    return clone


@contextmanager
def execution_lock(db, batch):
    """Session advisory lock survives trace commits and is released on process loss."""
    key = batch.id.int % (2**63 - 1)
    with _locks_guard:
        local = _locks.setdefault(batch.id, threading.Lock())
    if not local.acquire(blocking=False):
        raise HTTPException(409, "Пакет уже выполняется.")
    connection = None
    acquired = False
    try:
        if db.get_bind().dialect.name == "postgresql":
            connection = (
                db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT")
            )
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar()
            if not acquired:
                raise HTTPException(409, "Пакет уже выполняется.")
        yield
    finally:
        if connection is not None:
            try:
                if acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
            finally:
                connection.close()
        local.release()


def recover_interrupted(db, batch):
    for variant in variants(db, batch):
        for result in results(db, variant, status="running"):
            attempts = (
                db.query(BatchResultAttempt)
                .filter_by(result_id=result.id)
                .order_by(BatchResultAttempt.number)
                .all()
            )
            for attempt in attempts:
                run = db.get(ExecutionRun, attempt.execution_run_id)
                if run.status == "running":
                    trace = copy.deepcopy(run.response_trace) or {}
                    trace.setdefault("errors", []).append(
                        {
                            "stage": "execution",
                            "type": "ExecutionInterrupted",
                            "message": "Выполнение прервано до сохранения конечного статуса.",
                            "retryable": True,
                        }
                    )
                    for stage_attempt in trace.get("attempts", []):
                        if stage_attempt["status"] == "running":
                            stage_attempt["status"] = "error"
                    run.response_trace = trace
                    fail_run(db, run, "execution_interrupted")
            result.status = (
                "success" if attempts and run.status == "success" else "error"
            )
    db.commit()


async def execute_batch(
    db,
    batch,
    *,
    embedder_factory,
    store_factory,
    generate,
    retry_result_id=None,
    target_result_id=None,
    lock_held=False,
    question_timeout_seconds=None,
    execution_metadata=None,
):
    with nullcontext() if lock_held else execution_lock(db, batch):
        db.refresh(batch)
        recover_interrupted(db, batch)
        if retry_result_id is not None:
            selected = (
                db.query(BatchQuestionResult)
                .join(BatchVariant)
                .filter(
                    BatchVariant.batch_run_id == batch.id,
                    BatchQuestionResult.id == retry_result_id,
                )
                .first()
            )
            if selected is None:
                raise HTTPException(404, "Результат не найден.")
            if selected.status != "error":
                raise HTTPException(
                    409, "Технический повтор доступен только после ошибки."
                )
        batch.status, batch.finished_at = "running", None
        db.commit()
        try:
            version = db.get(QuestionSetVersion, batch.question_set_version_id)
            questions = {e["id"]: e for e in version.examples}
            for variant in variants(db, batch):
                # Credentials are runtime-only; all execution settings come from the snapshot.
                project = project_or_404(db, batch.project_id)
                frozen = SimpleNamespace(
                    id=project.id,
                    **variant.project_snapshot,
                    embedding_api_key=project.embedding_api_key,
                    llm_api_key=project.llm_api_key,
                )
                for result in results(db, variant, result_id=target_result_id):
                    if target_result_id is not None and result.id != target_result_id:
                        continue
                    if retry_result_id is not None:
                        if result.id != retry_result_id:
                            continue
                    elif result.status != "pending":
                        continue
                    applied = variant.applied_settings
                    payload = ChatRequest(
                        question=questions[str(result.question_example_id)]["question"],
                        question_set_version_id=version.id,
                        question_example_id=result.question_example_id,
                        top_k=applied["top_k"],
                        max_context_chars=applied["max_context_chars"],
                        score_threshold=applied["score_threshold"],
                        max_attempts=applied["max_attempts"],
                    )

                    def on_started(run):
                        if execution_metadata is not None:
                            run.details = {
                                **run.details,
                                "background_job": execution_metadata,
                            }
                        number = (
                            db.query(BatchResultAttempt)
                            .filter_by(result_id=result.id)
                            .count()
                            + 1
                        )
                        db.add(
                            BatchResultAttempt(
                                result_id=result.id,
                                number=number,
                                kind="initial" if number == 1 else "technical_retry",
                                execution_run_id=run.id,
                            )
                        )
                        result.status = "running"
                        db.commit()

                    try:
                        operation = execute_rag(
                            batch.project_id,
                            payload,
                            db,
                            embedder_factory=embedder_factory,
                            store_factory=store_factory,
                            generate=generate,
                            project_override=frozen,
                            prepared={
                                "settings": applied,
                                "index": db.get(IndexVersion, variant.index_version_id),
                            },
                            on_started=on_started,
                        )
                        if question_timeout_seconds is None:
                            await operation
                        else:
                            await asyncio.wait_for(
                                operation, timeout=question_timeout_seconds
                            )
                        result.status = "success"
                    except TimeoutError:
                        recover_interrupted(db, batch)
                        attempt = (
                            db.query(BatchResultAttempt)
                            .filter_by(result_id=result.id)
                            .order_by(BatchResultAttempt.number.desc())
                            .first()
                        )
                        if attempt is not None:
                            run = db.get(ExecutionRun, attempt.execution_run_id)
                            trace = copy.deepcopy(run.response_trace) or {}
                            trace.setdefault("errors", []).append(
                                {
                                    "stage": "execution",
                                    "type": "TimeoutError",
                                    "message": "Превышен лимит времени выполнения вопроса.",
                                    "retryable": True,
                                }
                            )
                            run.response_trace = trace
                            fail_run(db, run, "execution_timeout")
                        result.status = "error"
                    except HTTPException as exc:
                        if exc.status_code != 502:
                            raise
                        result.status = "error"
                    db.commit()
            statuses = status_counts(db, batch)
            batch.status = (
                "pending"
                if statuses["pending"] > 0
                else "completed_with_errors" if statuses["error"] > 0 else "completed"
            )
            batch.finished_at = (
                None if batch.status == "pending" else datetime.now(timezone.utc)
            )
            db.commit()
        except BaseException:
            db.rollback()
            batch.status = "interrupted"
            db.commit()
            raise
    return batch


def status_counts(db, batch):
    counts = {"pending": 0, "running": 0, "success": 0, "error": 0}
    for status, count in (
        db.query(BatchQuestionResult.status, func.count(BatchQuestionResult.id))
        .join(BatchVariant)
        .filter(BatchVariant.batch_run_id == batch.id)
        .group_by(BatchQuestionResult.status)
    ):
        counts[status] = count
    return counts


def batch_summary(db, batch):
    counts = status_counts(db, batch)
    return {
        "id": batch.id,
        "name": batch.name,
        "status": batch.status,
        "question_set_version_id": batch.question_set_version_id,
        "selection": batch.selection,
        "repeats": batch.repeats,
        "rerun_of_id": batch.rerun_of_id,
        "counts": counts,
        "created_at": batch.created_at,
        "finished_at": batch.finished_at,
        "cancel_requested": batch.cancel_requested,
        "active_job_id": batch.active_job_id,
    }


def batch_detail(db, batch):
    summary = batch_summary(db, batch)
    from backend.core.batch_queue import job_info

    summary["job"] = (
        job_info(db.get(BatchJob, batch.active_job_id)) if batch.active_job_id else None
    )
    version = db.get(QuestionSetVersion, batch.question_set_version_id)
    dataset = db.get(QuestionSet, version.question_set_id)
    summary["question_set"] = {
        "id": str(dataset.id),
        "name": dataset.name,
        "version_id": str(version.id),
        "number": version.number,
        "sha256": version.sha256,
        "documents_sha256": version.documents_sha256,
    }
    summary["metrics_status"] = "not_implemented"
    summary["variants"] = []
    for variant in variants(db, batch):
        item = {
            "id": variant.id,
            "name": variant.name,
            "position": variant.position,
            "configuration_version_id": variant.configuration_version_id,
            "index_version_id": variant.index_version_id,
            "applied_settings": variant.applied_settings,
            "index_status": db.get(IndexVersion, variant.index_version_id).status,
        }
        summary["variants"].append(item)
    from backend.core.metrics_analysis import differences

    summary["differences"] = differences(
        [v["applied_settings"] for v in summary["variants"]]
    )
    return summary


def result_detail(db, result, examples=None):
    from backend.core.batch_queue import job_info

    if examples is None:
        from backend.core.metrics_analysis import example_map

        variant = db.get(BatchVariant, result.variant_id)
        examples = example_map(db, db.get(BatchRun, variant.batch_run_id))
    attempts = (
        db.query(BatchResultAttempt)
        .filter_by(result_id=result.id)
        .order_by(BatchResultAttempt.number)
        .all()
    )
    from backend.core.metric_inputs import build_metric_input
    from backend.core.evaluators import checksum
    from backend.core.evaluation_checks import check_result

    metric_input = build_metric_input(db, result, examples=examples).model_dump(
        mode="json"
    )
    return {
        "id": result.id,
        "variant_id": result.variant_id,
        "metric_input": metric_input,
        "metric_input_sha256": checksum(metric_input),
        "evaluation_checks": [
            check_result(record)
            for record in db.query(EvaluationCheck)
            .filter_by(result_id=result.id)
            .order_by(EvaluationCheck.created_at, EvaluationCheck.id)
            .all()
        ],
        "question_example_id": result.question_example_id,
        "repeat_number": result.repeat_number,
        "example": copy.deepcopy(examples.get(str(result.question_example_id), {})),
        "status": result.status,
        "jobs": [
            job_info(job)
            for job in db.query(BatchJob)
            .filter_by(result_id=result.id)
            .order_by(BatchJob.created_at, BatchJob.id)
            .all()
        ],
        "attempts": [
            {
                "number": a.number,
                "kind": a.kind,
                "execution_run_id": a.execution_run_id,
                "status": (run := db.get(ExecutionRun, a.execution_run_id)).status,
                "configuration_version_id": run.configuration_version_id,
                "index_version_id": run.index_version_id,
                "question_set_version_id": run.question_set_version_id,
                "background_job": run.details.get("background_job"),
                "error_code": run.error_code,
                "response_trace": run.response_trace,
            }
            for a in attempts
        ],
    }
