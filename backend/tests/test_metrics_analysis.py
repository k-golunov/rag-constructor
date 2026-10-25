"""Analysis prepares real runs and exports traces, without metric scores."""

import csv
import io
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.db.models import BatchRun, ConfigurationVersion, ExecutionRun, IndexVersion
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


def payload(qs, version, **extra):
    qs.batches = f"{pipeline.BASE}/projects/{qs.project_id}/batches"
    return {
        "name": "Analysis",
        "question_set_version_id": version["id"],
        "split": "test",
        "variants": [
            {"name": "three", "settings": {"top_k": 3}},
            {"name": "five", "settings": {"top_k": 5}},
        ],
        **extra,
    }


def test_preview_uses_real_settings_and_is_read_only(qs):
    data = payload(qs, batches.reviewed(qs))
    with TestingSessionLocal() as db:
        before = db.query(IndexVersion).count()
    response = qs.client.post(qs.batches + "/preview", json=data)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["metrics_status"] == "not_implemented"
    assert plan["question_count"] == 2 and plan["result_count"] == 4
    assert plan["indexes_to_build"] == 0
    assert plan["differences"] == [{"parameter": "top_k", "values": [3, 5]}]
    assert plan["variants"][1]["index"]["shared_with"] == "three"
    assert qs.completion.call_count == 0
    with TestingSessionLocal() as db:
        assert db.query(IndexVersion).count() == before
        assert (
            db.query(ConfigurationVersion).count()
            == db.query(ExecutionRun).count()
            == db.query(BatchRun).count()
            == 0
        )
    data["variants"][1]["settings"]["chunk_size"] = 400
    changed = qs.client.post(qs.batches + "/preview", json=data).json()
    assert changed["indexes_to_build"] == 1
    assert changed["variants"][1]["index"]["requires_build"]
    assert {r["parameter"] for r in changed["differences"]} == {"top_k", "chunk_size"}


def test_preview_checksum_detects_changed_project_before_creating_run(qs):
    data = payload(qs, batches.reviewed(qs))
    plan = qs.client.post(qs.batches + "/preview", json=data).json()
    qs.client.patch(
        f"{pipeline.BASE}/projects/{qs.project_id}",
        json={"system_prompt": "Changed prompt"},
    )
    response = qs.client.post(
        qs.batches, json={**data, "preview_sha256": plan["preview_sha256"]}
    )
    assert response.status_code == 409
    with TestingSessionLocal() as db:
        assert db.query(BatchRun).count() == 0
    data["preview_sha256"] = qs.client.post(qs.batches + "/preview", json=data).json()[
        "preview_sha256"
    ]
    assert qs.client.post(qs.batches, json=data).status_code == 201


def test_saved_configuration_selection_keeps_effective_values(qs):
    version = batches.reviewed(qs)
    source = batches.make(
        qs,
        version,
        variants=[
            {
                "name": "Saved",
                "settings": {"top_k": 7, "max_attempts": 2, "score_threshold": 0.9},
            }
        ],
    )
    config_id = source["variants"][0]["configuration_version_id"]
    listing = qs.client.get(qs.batches + "/configurations?limit=1").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == config_id
    qs.client.patch(
        f"{pipeline.BASE}/projects/{qs.project_id}",
        json={"top_k": 40, "chunk_size": 1000, "llm_model": "changed"},
    )
    data = payload(
        qs, version, variants=[{"name": "Reuse", "configuration_version_id": config_id}]
    )
    plan = qs.client.post(qs.batches + "/preview", json=data).json()
    actual = plan["variants"][0]["applied_settings"]
    assert (
        actual["top_k"] == 7
        and actual["max_attempts"] == 2
        and actual["score_threshold"] == 0.9
    )
    assert actual["chunk_size"] == 800 and actual["llm_model"] == "control"
    created = qs.client.post(
        qs.batches, json={**data, "preview_sha256": plan["preview_sha256"]}
    ).json()
    assert created["variants"][0]["configuration_version_id"] == config_id
    assert "test-key" not in str(listing)


def test_results_include_immutable_reference_and_filter_on_server(qs):
    version = batches.reviewed(qs)
    batch = batches.make(qs, version)
    batches.run(qs, batch)
    filtered = qs.client.get(
        f"{qs.batches}/{batch['id']}/results",
        params={
            "variant_id": batch["variants"][0]["id"],
            "status": "success",
            "limit": 1,
        },
    ).json()
    assert filtered["total"] == 2 and len(filtered["items"]) == 1
    result = filtered["items"][0]
    assert result["example"]["reference_answer"] == "private-reference"
    trace = result["attempts"][0]["response_trace"]
    assert pipeline.ANSWER in trace["context"] and trace["answer"] == pipeline.ANSWER
    assert "private-reference" not in str(trace["messages"])
    edited = qs.client.patch(
        f"{qs.set_url}/examples/{version['examples'][0]['id']}",
        json={"base_version_id": version["id"], "reference_answer": "new reference"},
    )
    assert edited.status_code == 200
    exported = qs.client.get(f"{qs.batches}/{batch['id']}/export").json()
    assert exported["results"][0]["example"]["reference_answer"] == "private-reference"
    assert exported["question_set"]["version_id"] == version["id"]


def test_json_export_contains_all_rows_settings_and_unknown_usage(qs):
    batch = batches.make(qs, batches.reviewed(qs))
    batches.run(qs, batch)
    response = qs.client.get(f"{qs.batches}/{batch['id']}/export?format=json")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert not data["is_partial"] and data["metrics"] == {
        "status": "not_implemented",
        "scores": None,
    }
    assert (
        len(data["results"]) == 4
        and len(data["configuration_versions"]) == len(data["index_versions"]) == 2
    )
    assert data["index_versions"][0]["documents"][0]["sha256"]
    assert (
        data["results"][0]["attempts"][0]["response_trace"]["token_usage"]["generation"]
        is None
    )
    assert "test-key" not in response.text
    assert not any(
        "api_key" in config["settings"] for config in data["configuration_versions"]
    )


def test_partial_export_ignores_result_pagination(qs):
    batch = batches.make(qs, batches.reviewed(qs), repeats=10)
    page = qs.client.get(
        f"{qs.batches}/{batch['id']}/results?limit=1&status=pending"
    ).json()
    assert len(page["items"]) == 1
    data = qs.client.get(f"{qs.batches}/{batch['id']}/export").json()
    assert data["is_partial"] and len(data["results"]) == 40
    assert all(
        row["attempts"] == [] and row["status"] == "pending" for row in data["results"]
    )


def test_failed_and_retried_attempts_are_preserved_in_both_exports(qs, monkeypatch):
    batch = batches.make(qs, batches.reviewed(qs))
    original = qs.store.search
    monkeypatch.setattr(
        qs.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    batches.run(qs, batch)
    first = batches.answers(qs, batch)[0]
    monkeypatch.setattr(qs.store, "search", original)
    assert (
        qs.client.post(
            f"{qs.batches}/{batch['id']}/results/{first['id']}/retry"
        ).status_code
        == 202
    )
    batches.drain(qs, batch)
    data = qs.client.get(f"{qs.batches}/{batch['id']}/export").json()
    assert [a["status"] for a in data["results"][0]["attempts"]] == ["error", "success"]
    response = qs.client.get(f"{qs.batches}/{batch['id']}/export?format=csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 5
    assert (
        rows[0]["error_code"] == "retrieval_error"
        and rows[1]["answer"] == pipeline.ANSWER
    )


def test_csv_formula_cells_are_safe_while_json_keeps_original(qs):
    version = sets.create(
        qs, [{"question": "=2+3", "reference_answer": "@SUM(A1)", "split": "test"}]
    )
    version = sets.approve(qs, version)
    batch = batches.make(qs, version, variants=[{"name": "+variant"}])
    response = qs.client.get(f"{qs.batches}/{batch['id']}/export?format=csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert (
        rows[0]["question"] == "'=2+3"
        and rows[0]["reference_answer"] == "'@SUM(A1)"
        and rows[0]["variant"] == "'+variant"
    )
    data = qs.client.get(f"{qs.batches}/{batch['id']}/export").json()
    assert data["results"][0]["example"]["question"] == "=2+3"


def test_analysis_endpoints_keep_project_scope(qs):
    version = batches.reviewed(qs)
    batch = batches.make(qs, version)
    wrong = f"{pipeline.BASE}/projects/{uuid4()}/batches"
    assert qs.client.get(wrong + "/configurations").status_code == 404
    assert qs.client.get(wrong + f"/{batch['id']}/export").status_code == 404
    data = payload(
        qs,
        version,
        variants=[{"name": "bad", "configuration_version_id": str(uuid4())}],
    )
    assert qs.client.post(qs.batches + "/preview", json=data).status_code == 404
    data = payload(
        qs, version, variants=[{"name": "bad", "settings": {"temperature": 0.2}}]
    )
    assert qs.client.post(qs.batches + "/preview", json=data).status_code == 422


def test_export_during_retry_is_partial_even_without_pending_questions(qs, monkeypatch):
    from backend.core.batch_queue import perform_job
    from backend.api import chat
    import asyncio

    monkeypatch.setattr(settings, "BATCH_JOB_MAX_ATTEMPTS", 2)
    version = batches.reviewed(qs)
    batch = batches.make(
        qs,
        version,
        variants=[{"name": "retry"}],
        example_ids=[version["examples"][0]["id"]],
    )
    monkeypatch.setattr(
        qs.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    qs.client.post(f"{qs.batches}/{batch['id']}/execute")
    with TestingSessionLocal() as db:
        record = db.get(BatchRun, UUID(batch["id"]))
        assert (
            asyncio.run(
                perform_job(
                    db,
                    record.active_job_id,
                    embedder_factory=chat.build_embedder,
                    store_factory=chat.build_vector_store,
                    generate=chat._generate_traced,
                )
            )
            == "retry"
        )
    data = qs.client.get(f"{qs.batches}/{batch['id']}/export").json()
    assert data["is_partial"] and data["batch"]["status"] == "queued"
