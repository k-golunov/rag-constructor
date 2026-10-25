"""Persist independent checks of saved inputs; never schedules answer collection."""

from fastapi import HTTPException
from pydantic import ValidationError

from backend.core.evaluators import REGISTRY, checksum, evaluate_saved_input
from backend.core.metric_inputs import build_metric_input, execution_secrets
from backend.core.response_trace import redact
from backend.db.models import BatchRun, BatchVariant, EvaluationCheck, Project


def check_result(record):
    return {
        "id": record.id,
        "result_id": record.result_id,
        "execution_run_id": record.execution_run_id,
        "input_sha256": record.input_sha256,
        "input": record.input_snapshot,
        "evaluator_descriptor": record.evaluator_descriptor,
        "output": record.output,
        "created_at": record.created_at,
    }


async def create_check(db, result, payload):
    evaluator = REGISTRY.get((payload.evaluator_id, payload.evaluator_version))
    if evaluator is None:
        raise HTTPException(
            422,
            "Оценщик с указанной версией не подключён. Сейчас доступна только тестовая проверка формата.",
        )
    variant = db.get(BatchVariant, result.variant_id)
    batch = db.get(BatchRun, variant.batch_run_id)
    secrets = execution_secrets(db.get(Project, batch.project_id))
    if redact(payload.parameters, secrets) != payload.parameters:
        raise HTTPException(
            422,
            "API-ключи и credentials не допускаются в сохраняемых параметрах оценщика.",
        )
    sample = build_metric_input(db, result, payload.execution_run_id)
    try:
        output = await evaluate_saved_input(sample, evaluator, payload.parameters)
    except ValidationError as exc:
        raise HTTPException(
            422,
            exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc
    output_data = output.model_dump(mode="json")
    if redact(output_data, secrets) != output_data:
        raise HTTPException(
            422, "Результат оценщика содержит credentials; проверка не сохранена."
        )
    record = EvaluationCheck(
        result_id=result.id,
        execution_run_id=sample.provenance.execution_run_id,
        input_sha256=checksum(sample.model_dump(mode="json")),
        input_snapshot=sample.model_dump(mode="json"),
        evaluator_descriptor=evaluator.descriptor.model_dump(mode="json"),
        output=output_data,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return check_result(record)
