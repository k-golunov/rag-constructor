"""Versioned handoff to future evaluators; no quality measure is implemented."""

from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Evidence(Contract):
    document_version_id: UUID
    document_sha256: str
    file_name: str
    quote: str
    start_char: int | None = None
    end_char: int | None = None
    source_text_sha256: str | None = None
    parser: str | None = None


class RetrievedChunk(Contract):
    id: str
    rank: int = Field(ge=1)
    text: str
    score: float | None = None
    source: str | None = None
    document_version_id: UUID | None = None
    document_sha256: str | None = None


class Provenance(Contract):
    project_id: UUID
    batch_id: UUID
    variant_id: UUID
    result_id: UUID
    question_example_id: UUID
    repeat_number: int = Field(ge=1)
    execution_run_id: UUID | None = None
    configuration_version_id: UUID | None = None
    configuration_sha256: str | None = None
    index_version_id: UUID | None = None
    index_fingerprint: str | None = None
    documents_sha256: str | None = None
    question_set_version_id: UUID
    question_set_sha256: str
    source_trace_sha256: str | None = None
    source_trace_schema_version: int | None = None
    question_isolated: bool | None = None


class MetricInput(Contract):
    schema_version: Literal[1] = 1
    question: str
    answer: str | None = None
    context: str | None = None
    reference_answer: str | None = None
    evidence: list[Evidence] | None = None
    retrieved_chunks: list[RetrievedChunk] | None = None
    context_chunk_ids: list[str] | None = None
    unanswerable: bool | None = None
    category: str | None = None
    processing_status: Literal["pending", "running", "success", "error"]
    retrieval_status: Literal["not_started", "success", "no_context", "error"]
    provenance: Provenance
    integrity_errors: list[str] = Field(default_factory=list)
    redaction_applied: bool = False


class DataRequirement(Contract):
    field: Literal[
        "question",
        "answer",
        "context",
        "reference_answer",
        "evidence",
        "retrieved_chunks",
    ]
    condition: Literal["present", "non_empty"] = "non_empty"
    description: str = Field(min_length=1)


class EvaluatorDescriptor(Contract):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str
    description: str
    kind: Literal["metric", "transport_validation"]
    input_schema_versions: list[int] = Field(default_factory=lambda: [1])
    requirements: list[DataRequirement]
    parameters_schema: dict[str, JsonValue]


class Assessment(Contract):
    status: Literal["scored", "validated", "skipped", "error"]
    value: float | None = Field(default=None, strict=True, allow_inf_nan=False)
    explanation: str = Field(min_length=1)
    reason_code: str | None = None
    missing_data: list[str] = Field(default_factory=list)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def value_matches_status(self):
        if self.status == "scored" and self.value is None:
            raise ValueError("A scored result requires a value")
        if self.status != "scored" and self.value is not None:
            raise ValueError("A non-scored result must have value=null")
        return self


class EvaluatorIdentity(Contract):
    id: str
    version: str
    kind: Literal["metric", "transport_validation"]
    parameters: dict[str, JsonValue]


class MetricResult(Assessment):
    schema_version: Literal[1] = 1
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluator: EvaluatorIdentity


class EvaluationCheckRequest(Contract):
    evaluator_id: str = "transport_stub"
    evaluator_version: str = "1.0.0"
    execution_run_id: UUID | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class MetricInputEnvelope(Contract):
    input_sha256: str
    input: MetricInput


class EvaluationCheckResponse(Contract):
    id: UUID
    result_id: UUID
    execution_run_id: UUID | None
    input_sha256: str
    input: MetricInput
    evaluator_descriptor: EvaluatorDescriptor
    output: MetricResult
    created_at: datetime


class EvaluationCheckList(Contract):
    total: int
    items: list[EvaluationCheckResponse]


class EvaluatorCatalog(Contract):
    metrics_status: Literal["not_implemented"] = "not_implemented"
    evaluators: list[EvaluatorDescriptor]
