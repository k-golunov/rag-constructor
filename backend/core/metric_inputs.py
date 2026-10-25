"""Read-only adapter from saved attempts to the evaluator contract. No RAG calls."""

import os
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from backend.config import settings
from backend.core.evaluators import checksum
from backend.core.response_trace import redact
from backend.db.metric_schemas import Evidence, MetricInput, Provenance, RetrievedChunk
from backend.db.models import (
    BatchRun,
    BatchVariant,
    BatchQuestionResult,
    BatchResultAttempt,
    ConfigurationVersion,
    ExecutionRun,
    IndexVersion,
    Project,
    QuestionSetVersion,
)


def execution_secrets(project):
    return (
        project.embedding_api_key,
        project.llm_api_key,
        settings.OPENAI_API_KEY,
        settings.QDRANT_API_KEY,
        os.getenv("OPENAI_API_KEY"),
    )


def scoped_result(db, project_id, batch_id, result_id):
    result = (
        db.query(BatchQuestionResult)
        .join(BatchVariant)
        .join(BatchRun)
        .filter(
            BatchRun.project_id == project_id,
            BatchRun.id == batch_id,
            BatchQuestionResult.id == result_id,
        )
        .first()
    )
    if result is None:
        raise HTTPException(404, "Результат не найден в запуске проекта.")
    return result


def build_metric_input(db, result, execution_run_id=None, examples=None):
    variant = db.get(BatchVariant, result.variant_id)
    batch = db.get(BatchRun, variant.batch_run_id)
    version = db.get(QuestionSetVersion, batch.question_set_version_id)
    example = (examples or {e["id"]: e for e in version.examples}).get(
        str(result.question_example_id), {}
    )
    attempts = db.query(BatchResultAttempt).filter_by(result_id=result.id)
    if execution_run_id is not None:
        attempt = attempts.filter_by(execution_run_id=execution_run_id).first()
        if attempt is None:
            raise HTTPException(404, "Попытка не принадлежит этому результату.")
    else:
        attempt = attempts.order_by(BatchResultAttempt.number.desc()).first()
    run = db.get(ExecutionRun, attempt.execution_run_id) if attempt else None
    trace = run.response_trace if run and isinstance(run.response_trace, dict) else {}
    errors = []
    if not example:
        errors.append("question_example_missing")
    if run:
        expected = {
            "project_id": batch.project_id,
            "question_set_version_id": version.id,
            "question_example_id": result.question_example_id,
            "configuration_version_id": variant.configuration_version_id,
            "index_version_id": variant.index_version_id,
        }
        errors.extend(
            key + "_mismatch"
            for key, value in expected.items()
            if getattr(run, key) != value
        )
        if run.mode != "rag":
            errors.append("execution_mode_mismatch")
        if trace and trace.get("question") != example.get("question"):
            errors.append("question_mismatch")
        if trace.get("history") not in (None, []):
            errors.append("question_history_not_isolated")
    config = (
        db.get(ConfigurationVersion, run.configuration_version_id)
        if run and run.configuration_version_id
        else None
    )
    index = (
        db.get(IndexVersion, run.index_version_id)
        if run and run.index_version_id
        else None
    )
    if index and index.documents_sha256 != version.documents_sha256:
        errors.append("index_documents_mismatch")
    if config and config.project_id != batch.project_id:
        errors.append("configuration_project_mismatch")
    if index and index.project_id != batch.project_id:
        errors.append("index_project_mismatch")
    evidence = None
    if example.get("evidence") is not None:
        try:
            evidence = [Evidence.model_validate(value) for value in example["evidence"]]
        except ValidationError:
            errors.append("evidence_invalid")
    chunks = None
    if trace.get("retrieved_chunks") is not None:
        chunks = []
        for chunk in trace["retrieved_chunks"]:
            try:
                chunks.append(
                    RetrievedChunk.model_validate(
                        {key: chunk.get(key) for key in RetrievedChunk.model_fields}
                    )
                )
            except (ValidationError, AttributeError):
                errors.append("retrieved_chunk_invalid")
    answer = trace.get("answer") if isinstance(trace.get("answer"), str) else None
    context = trace.get("context") if isinstance(trace.get("context"), str) else None
    sample = MetricInput(
        question=trace.get("question", example.get("question", "")),
        answer=answer,
        context=context,
        reference_answer=example.get("reference_answer"),
        evidence=evidence,
        retrieved_chunks=chunks,
        context_chunk_ids=trace.get("context_chunk_ids"),
        unanswerable=example.get("unanswerable"),
        category=example.get("category"),
        processing_status=run.status if run else result.status,
        retrieval_status=trace.get("retrieval_status", "not_started"),
        provenance=Provenance(
            project_id=batch.project_id,
            batch_id=batch.id,
            variant_id=variant.id,
            result_id=result.id,
            question_example_id=result.question_example_id,
            repeat_number=result.repeat_number,
            execution_run_id=run.id if run else None,
            configuration_version_id=config.id if config else None,
            configuration_sha256=config.sha256 if config else None,
            index_version_id=index.id if index else None,
            index_fingerprint=index.fingerprint if index else None,
            documents_sha256=index.documents_sha256 if index else None,
            question_set_version_id=version.id,
            question_set_sha256=version.sha256,
            source_trace_sha256=checksum(trace) if trace else None,
            source_trace_schema_version=trace.get("schema_version"),
            question_isolated=(
                trace.get("history") == [] if "history" in trace else None
            ),
        ),
        integrity_errors=errors,
    )
    data = sample.model_dump(mode="json")
    cleaned = redact(data, execution_secrets(db.get(Project, batch.project_id)))
    cleaned["redaction_applied"] = cleaned != data or trace.get(
        "redaction_applied", False
    )
    return MetricInput.model_validate(cleaned)
