"""Pure evaluator interface. It has no access to generation or retrieval services."""

import hashlib
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from backend.db.metric_schemas import (
    Assessment,
    DataRequirement,
    EvaluatorDescriptor,
    EvaluatorIdentity,
    MetricInput,
    MetricResult,
)


def checksum(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class Evaluator(Protocol):
    descriptor: EvaluatorDescriptor
    parameters_model: type[BaseModel]

    async def evaluate(
        self, sample: MetricInput, parameters: BaseModel
    ) -> Assessment: ...


class StubParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransportStub:
    parameters_model = StubParameters
    descriptor = EvaluatorDescriptor(
        id="transport_stub",
        version="1.0.0",
        title="Проверка передачи данных",
        description="Тестовая заглушка проверяет формат и происхождение данных. Качество не оценивается.",
        kind="transport_validation",
        requirements=[
            DataRequirement(field="question", description="Сохранённый вопрос"),
            DataRequirement(field="answer", description="Успешно сохранённый ответ"),
            DataRequirement(
                field="context",
                condition="present",
                description="Фактически переданный контекст, включая пустой",
            ),
        ],
        parameters_schema=StubParameters.model_json_schema(),
    )

    async def evaluate(self, sample, parameters):
        return Assessment(
            status="validated",
            value=None,
            explanation="Вопрос, ответ и фактический контекст переданы в тестовую заглушку. Расчёт качества не выполнялся.",
            details={
                "received_fields": [
                    "question",
                    "answer",
                    "context",
                    "reference_answer",
                    "evidence",
                ],
                "reference_provided": sample.reference_answer is not None,
                "evidence_provided": bool(sample.evidence),
            },
        )


REGISTRY = {("transport_stub", "1.0.0"): TransportStub()}


def missing_requirements(sample, descriptor):
    missing = []
    for requirement in descriptor.requirements:
        value = getattr(sample, requirement.field)
        empty = (
            value is None
            or (isinstance(value, str) and not value.strip())
            or value == []
        )
        if value is None or (requirement.condition == "non_empty" and empty):
            missing.append(requirement.field)
    return missing


async def evaluate_saved_input(
    sample: MetricInput, evaluator: Evaluator, parameters=None
):
    """Validate requirements, then call only the supplied evaluator on a detached copy."""
    descriptor = evaluator.descriptor
    actual = evaluator.parameters_model.model_validate(
        parameters if parameters is not None else {}
    )
    identity = EvaluatorIdentity(
        id=descriptor.id,
        version=descriptor.version,
        kind=descriptor.kind,
        parameters=actual.model_dump(mode="json"),
    )
    digest = checksum(sample.model_dump(mode="json"))
    if sample.processing_status != "success":
        assessment = Assessment(
            status="skipped",
            explanation="Ответ ещё не сохранён успешно; оценка не запускалась.",
            reason_code="answer_not_successful",
        )
    elif sample.integrity_errors:
        assessment = Assessment(
            status="skipped",
            explanation="Нарушена связь ответа с исходными данными.",
            reason_code="integrity_error",
            details={"integrity_errors": sample.integrity_errors},
        )
    elif sample.schema_version not in descriptor.input_schema_versions:
        assessment = Assessment(
            status="skipped",
            explanation="Версия входного формата не поддерживается оценщиком.",
            reason_code="unsupported_input_version",
        )
    else:
        lineage = sample.provenance
        missing = [
            "provenance." + key
            for key in (
                "execution_run_id",
                "configuration_version_id",
                "index_version_id",
                "source_trace_sha256",
            )
            if getattr(lineage, key) is None
        ]
        if lineage.question_isolated is not True:
            missing.append("provenance.question_isolated")
        if lineage.source_trace_schema_version != 1:
            missing.append("provenance.source_trace_schema_version")
        missing.extend(missing_requirements(sample, descriptor))
        if missing:
            assessment = Assessment(
                status="skipped",
                explanation="Недостаточно обязательных данных для оценщика.",
                reason_code="missing_data",
                missing_data=missing,
            )
        else:
            detached = sample.model_copy(deep=True)
            try:
                assessment = Assessment.model_validate(
                    await evaluator.evaluate(detached, actual)
                )
                if checksum(detached.model_dump(mode="json")) != digest:
                    raise ValueError("Evaluator modified its input")
                if (
                    descriptor.kind == "transport_validation"
                    and assessment.status == "scored"
                ):
                    raise ValueError("A transport check cannot produce a metric score")
            except Exception as exc:
                assessment = Assessment(
                    status="error",
                    explanation="Ошибка модуля оценщика; исходный ответ не изменён.",
                    reason_code="evaluator_error",
                    details={"error_type": type(exc).__name__},
                )
    return MetricResult(
        **assessment.model_dump(), evaluator=identity, input_sha256=digest
    )
