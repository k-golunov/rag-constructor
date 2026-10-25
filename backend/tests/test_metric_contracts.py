"""Saved answer → versioned evaluator input → test-only independent check."""

import asyncio
import copy
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.config import settings
from backend.core.evaluators import TransportStub, checksum, evaluate_saved_input
from backend.db.metric_schemas import (
    Assessment,
    DataRequirement,
    EvaluatorDescriptor,
    MetricInput,
    MetricResult,
)
from backend.db.models import BatchQuestionResult, EvaluationCheck, ExecutionRun
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_batches as batches
from backend.tests import test_question_sets as sets
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
qs = sets.qs


@pytest.fixture(autouse=True)
def isolated_queue(monkeypatch):
    monkeypatch.setattr(
        "backend.api.batches.publish_job", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(settings, "BATCH_JOB_MAX_ATTEMPTS", 1)


@pytest.fixture
def saved(qs):
    version = batches.reviewed(qs)
    batch = batches.make(qs, version)
    batches.run(qs, batch)
    row = batches.answers(qs, batch)[0]
    return SimpleNamespace(
        qs=qs,
        batch=batch,
        version=version,
        row=row,
        url=f"{qs.batches}/{batch['id']}/results/{row['id']}",
    )


def test_saved_input_handoff_does_not_call_retrieval_or_generation(saved, monkeypatch):
    qs = saved.qs
    original_calls = qs.completion.call_count
    original_embeddings = qs.embedder.embed.call_count
    for name in ("build_embedder", "build_llm", "build_vector_store"):
        monkeypatch.setattr(
            f"backend.api.chat.{name}",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("No regeneration allowed")
            ),
        )
    monkeypatch.setattr(
        qs.store,
        "search",
        AsyncMock(side_effect=AssertionError("No retrieval allowed")),
    )
    sample = qs.client.get(saved.url + "/metric-input").json()
    contract = MetricInput.model_validate(sample["input"])
    assert sample["input_sha256"] == checksum(contract.model_dump(mode="json"))
    assert contract.answer == pipeline.ANSWER and pipeline.ANSWER in contract.context
    assert (
        contract.reference_answer == "private-reference"
        and contract.provenance.question_isolated
    )
    assert (
        str(contract.provenance.index_version_id)
        == saved.batch["variants"][0]["index_version_id"]
    )
    response = qs.client.post(saved.url + "/evaluation-checks", json={})
    assert response.status_code == 201, response.text
    record = response.json()
    assert (
        record["output"]["status"] == "validated" and record["output"]["value"] is None
    )
    assert record["output"]["evaluator"] == {
        "id": "transport_stub",
        "version": "1.0.0",
        "kind": "transport_validation",
        "parameters": {},
    }
    assert record["input_sha256"] == sample["input_sha256"]
    assert (
        qs.completion.call_count == original_calls
        and qs.embedder.embed.call_count == original_embeddings
    )
    qs.store.search.assert_not_called()
    with TestingSessionLocal() as db:
        assert (
            db.query(ExecutionRun).count() == 4
            and db.query(EvaluationCheck).count() == 1
        )
        assert (
            db.get(ExecutionRun, UUID(record["execution_run_id"])).status == "success"
        )
        assert db.get(BatchQuestionResult, UUID(saved.row["id"])).status == "success"


def test_descriptors_expose_requirements_and_json_schemas(saved):
    project = f"{pipeline.BASE}/projects/{saved.qs.project_id}"
    catalog = saved.qs.client.get(project + "/metric-evaluators").json()
    assert catalog["metrics_status"] == "not_implemented"
    assert len(catalog["evaluators"]) == 1
    stub = catalog["evaluators"][0]
    assert stub["kind"] == "transport_validation"
    assert {r["field"] for r in stub["requirements"]} == {
        "question",
        "answer",
        "context",
    }
    schemas = saved.qs.client.get(project + "/metric-contracts").json()
    assert schemas["schema_version"] == 1
    assert {
        "question",
        "answer",
        "context",
        "reference_answer",
        "evidence",
        "provenance",
    }.issubset(schemas["input"]["properties"])
    assert {"value", "explanation", "status", "evaluator", "input_sha256"}.issubset(
        schemas["result"]["properties"]
    )


def test_empty_context_and_unknown_reference_are_preserved(saved, monkeypatch):
    qs = saved.qs
    monkeypatch.setattr(qs.store, "search", AsyncMock(return_value=[]))
    clone = qs.client.post(f"{qs.batches}/{saved.batch['id']}/rerun").json()
    batches.run(qs, clone)
    row = batches.answers(qs, clone)[1]  # unanswerable example without a reference
    url = f"{qs.batches}/{clone['id']}/results/{row['id']}"
    check = qs.client.post(url + "/evaluation-checks", json={}).json()
    assert (
        check["input"]["context"] == ""
        and check["input"]["retrieval_status"] == "no_context"
    )
    assert (
        check["input"]["reference_answer"] is None and check["input"]["evidence"] == []
    )
    assert check["input"]["unanswerable"]
    assert check["output"]["status"] == "validated" and check["output"]["value"] is None


def test_future_evaluator_requirements_skip_missing_data(saved):
    sample = MetricInput.model_validate(saved.row["metric_input"])

    class Parameters(BaseModel):
        model_config = ConfigDict(extra="forbid")
        locale: str = "ru"

    class FutureEvaluator:
        parameters_model = Parameters
        descriptor = EvaluatorDescriptor(
            id="future-reference",
            version="design-only",
            title="Test contract",
            description="No metric implementation",
            kind="metric",
            requirements=[
                DataRequirement(
                    field="reference_answer", description="A human reference"
                ),
                DataRequirement(field="evidence", description="Source evidence"),
            ],
            parameters_schema=Parameters.model_json_schema(),
        )
        evaluate = AsyncMock(side_effect=AssertionError("Missing required data"))

    evaluator = FutureEvaluator()
    output = asyncio.run(evaluate_saved_input(sample, evaluator))
    assert (
        output.status == "skipped"
        and output.value is None
        and output.missing_data == ["evidence"]
    )
    assert (
        output.evaluator.parameters == {"locale": "ru"}
        and output.evaluator.version == "design-only"
    )
    evaluator.evaluate.assert_not_called()


def test_unsuccessful_and_legacy_answers_never_look_scored(saved, monkeypatch):
    qs = saved.qs
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(saved.row["attempts"][0]["execution_run_id"]))
        run.status = "error"
        db.commit()
    check = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    assert (
        check["output"]["status"] == "skipped"
        and check["output"]["reason_code"] == "answer_not_successful"
    )
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(saved.row["attempts"][0]["execution_run_id"]))
        run.status = "success"
        run.response_trace = None
        db.commit()
    legacy = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    assert legacy["input"]["answer"] is None and legacy["input"]["context"] is None
    assert (
        legacy["output"]["status"] == "skipped"
        and "context" in legacy["output"]["missing_data"]
    )
    assert legacy["output"]["value"] is None


def test_question_isolation_and_index_mismatches_block_evaluator(saved):
    qs = saved.qs
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(saved.row["attempts"][0]["execution_run_id"]))
        trace = copy.deepcopy(run.response_trace)
        trace["history"] = [{"role": "assistant", "content": "previous question"}]
        run.response_trace = trace
        run.index_version_id = UUID(saved.batch["variants"][1]["index_version_id"])
        db.commit()
    record = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    assert record["output"]["status"] == "skipped"
    assert {"question_history_not_isolated", "index_version_id_mismatch"}.issubset(
        record["input"]["integrity_errors"]
    )
    assert record["output"]["reason_code"] == "integrity_error"


def test_evaluator_failure_is_separate_from_answer_status(saved, monkeypatch):
    from backend.core.evaluators import REGISTRY

    evaluator = REGISTRY[("transport_stub", "1.0.0")]
    monkeypatch.setattr(
        evaluator,
        "evaluate",
        AsyncMock(side_effect=RuntimeError("test-key must not be persisted")),
    )
    record = saved.qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    assert record["output"]["status"] == "error" and record["output"]["value"] is None
    assert "test-key" not in str(record)
    with TestingSessionLocal() as db:
        assert (
            db.get(ExecutionRun, UUID(record["execution_run_id"])).status == "success"
        )
        assert db.get(BatchQuestionResult, UUID(saved.row["id"])).status == "success"


def test_checks_keep_old_snapshots_and_export_both_contract_and_outcomes(saved):
    qs = saved.qs
    first = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    qs.client.patch(
        f"{pipeline.BASE}/projects/{qs.project_id}",
        json={"chunk_size": 1200, "system_prompt": "A new project prompt"},
    )
    edited = qs.client.patch(
        f"{qs.set_url}/examples/{saved.version['examples'][0]['id']}",
        json={
            "base_version_id": saved.version["id"],
            "reference_answer": "A new reference",
        },
    )
    assert edited.status_code == 200
    second = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    assert (
        second["input_sha256"] == first["input_sha256"] and second["id"] != first["id"]
    )
    assert second["input"]["reference_answer"] == "private-reference"
    listed = qs.client.get(saved.url + "/evaluation-checks").json()
    assert listed["total"] == 2
    exported = qs.client.get(f"{qs.batches}/{saved.batch['id']}/export").json()
    row = exported["results"][0]
    assert exported["metrics"] == {"status": "not_implemented", "scores": None}
    assert exported["evaluation_contract"]["input_schema_version"] == 1
    assert (
        row["metric_input_sha256"] == first["input_sha256"]
        and len(row["evaluation_checks"]) == 2
    )
    assert row["evaluation_checks"][0]["output"]["value"] is None
    with TestingSessionLocal() as db:
        record = db.get(EvaluationCheck, UUID(first["id"]))
        record.output = {"value": 1}
        with pytest.raises(ValueError, match="неизменяема"):
            db.commit()
        db.rollback()


def test_scope_attempt_selection_unknown_versions_and_secret_parameters(saved):
    qs = saved.qs
    other = batches.answers(qs, saved.batch)[1]
    foreign_attempt = other["attempts"][0]["execution_run_id"]
    assert (
        qs.client.get(
            saved.url + "/metric-input", params={"execution_run_id": foreign_attempt}
        ).status_code
        == 404
    )
    assert (
        qs.client.post(
            saved.url + "/evaluation-checks", json={"execution_run_id": foreign_attempt}
        ).status_code
        == 404
    )
    wrong = saved.url.replace(qs.project_id, str(uuid4()))
    assert qs.client.get(wrong + "/metric-input").status_code == 404
    assert qs.client.post(wrong + "/evaluation-checks", json={}).status_code == 404
    assert qs.client.get(wrong + "/evaluation-checks").status_code == 404
    assert (
        qs.client.post(
            saved.url + "/evaluation-checks", json={"evaluator_version": "999"}
        ).status_code
        == 422
    )
    assert (
        qs.client.post(
            saved.url + "/evaluation-checks",
            json={"parameters": {"api_key": "test-key"}},
        ).status_code
        == 422
    )
    assert (
        qs.client.post(
            saved.url + "/evaluation-checks", json={"parameters": {"unknown": 1}}
        ).status_code
        == 422
    )
    with TestingSessionLocal() as db:
        assert db.query(EvaluationCheck).count() == 0


@pytest.mark.parametrize(
    "status,value",
    [
        ("validated", 0),
        ("skipped", 0),
        ("error", 0),
        ("scored", None),
        ("scored", float("nan")),
        ("scored", float("inf")),
        ("scored", True),
    ],
)
def test_result_schema_never_coerces_unknown_or_failed_values_to_scores(status, value):
    with pytest.raises(ValidationError):
        Assessment(status=status, value=value, explanation="Contract test only")


def test_explicit_zero_is_supported_only_in_the_future_scored_format():
    value = Assessment(
        status="scored", value=0, explanation="Schema test; no metric was executed"
    )
    assert value.value == 0
    assert (
        Assessment(status="validated", explanation="No quality calculation").value
        is None
    )


def test_stub_cannot_return_a_score_or_modify_its_input(saved):
    sample = MetricInput.model_validate(saved.row["metric_input"])
    stub = TransportStub()
    stub.evaluate = AsyncMock(
        return_value=Assessment(
            status="scored", value=1, explanation="Invalid transport output"
        )
    )
    result = asyncio.run(evaluate_saved_input(sample, stub))
    assert result.status == "error" and result.value is None
    assert MetricResult.model_validate_json(
        result.model_dump_json()
    ).input_sha256 == checksum(sample.model_dump(mode="json"))


def test_evidence_contract_is_independent_of_chunking(qs):
    version = sets.create(qs)
    version = sets.approve(qs, version)
    batch = batches.make(qs, version)
    batches.run(qs, batch)
    rows = batches.answers(qs, batch)
    first, second = rows[0]["metric_input"], rows[1]["metric_input"]
    assert (
        first["provenance"]["index_version_id"]
        != second["provenance"]["index_version_id"]
    )
    assert first["evidence"] == second["evidence"]
    evidence = first["evidence"][0]
    assert (
        evidence["document_version_id"] == qs.document_id
        and evidence["quote"] == pipeline.ANSWER
    )
    assert (
        evidence["start_char"] < evidence["end_char"] and evidence["source_text_sha256"]
    )


def test_evaluation_migration_preserves_generation_tables():
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    from backend.db.database import Base

    migration = importlib.import_module(
        "backend.alembic.versions.20261025_0010_evaluation_checks"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(
            connection,
            tables=[
                t for t in Base.metadata.sorted_tables if t.name != "evaluation_checks"
            ],
        )
        before = set(inspect(connection).get_table_names())
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == before | {
                "evaluation_checks"
            }
            assert {
                column["name"]
                for column in inspect(connection).get_columns("evaluation_checks")
            } == set(EvaluationCheck.__table__.columns.keys())
            migration.downgrade()
        assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_retry_selects_latest_attempt_but_old_check_stays_linked(saved, monkeypatch):
    qs = saved.qs
    run_id = saved.row["attempts"][0]["execution_run_id"]
    first = qs.client.post(saved.url + "/evaluation-checks", json={}).json()
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(run_id))
        run.status = "error"
        result = db.get(BatchQuestionResult, UUID(saved.row["id"]))
        result.status = "error"
        db.commit()
    assert qs.client.post(saved.url + "/retry").status_code == 202
    batches.drain(qs, saved.batch)
    current = qs.client.get(saved.url + "/metric-input").json()
    assert current["input"]["provenance"]["execution_run_id"] != run_id
    assert (
        qs.client.get(saved.url + "/evaluation-checks").json()["items"][0][
            "input_sha256"
        ]
        == first["input_sha256"]
    )
    assert (
        first["input"]["processing_status"] == "success"
    )  # immutable snapshot from before the source test mutation
    export = qs.client.get(f"{qs.batches}/{saved.batch['id']}/export?format=csv")
    import csv
    import io
    import json

    rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert json.loads(rows[0]["evaluation_checks"])[0]["execution_run_id"] == run_id
    assert (
        json.loads(rows[0]["metric_input"])["provenance"]["execution_run_id"] != run_id
    )


def test_project_deletion_with_evaluation_checks_respects_foreign_keys(saved):
    from backend.tests.conftest import engine_test

    qs = saved.qs
    assert qs.client.post(saved.url + "/evaluation-checks", json={}).status_code == 201
    with engine_test.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    try:
        response = qs.client.delete(f"{pipeline.BASE}/projects/{qs.project_id}")
        assert response.status_code == 204, response.text
        with TestingSessionLocal() as db:
            assert db.query(EvaluationCheck).count() == 0
            assert db.query(ExecutionRun).count() == 0
    finally:
        with engine_test.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
