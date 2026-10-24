"""Question-set authoring, source evidence, human review and pinned execution."""

import csv
import io
import json
from uuid import UUID, uuid4

import pytest

from backend.db.models import ChatHistory, ExecutionRun, QuestionSetVersion
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag


@pytest.fixture
def qs(rag):
    uploaded = pipeline.upload(rag).json()
    rag.document_id = uploaded["document_version_id"]
    rag.sets_url = f"{pipeline.BASE}/projects/{rag.project_id}/question-sets"
    return rag


def create(qs, examples=None):
    if examples is None:
        examples = [
            {
                "question": pipeline.QUESTION,
                "reference_answer": "evaluation-only-reference",
                "evidence": [
                    {"document_version_id": qs.document_id, "quote": pipeline.ANSWER}
                ],
                "category": "lookup",
                "split": "test",
            }
        ]
    response = qs.client.post(
        qs.sets_url, json={"name": "Control set", "examples": examples}
    )
    assert response.status_code == 201, response.text
    result = response.json()
    qs.set_url = f"{qs.sets_url}/{result['id']}"
    return result["version"]


def approve(qs, version, example_id=None, status="approved"):
    example_id = example_id or version["examples"][0]["id"]
    response = qs.client.post(
        f"{qs.set_url}/examples/{example_id}/review",
        json={
            "base_version_id": version["id"],
            "status": status,
            "reviewer": "Test reviewer",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def execute(qs, version, **extra):
    example = version["examples"][0]
    return qs.client.post(
        qs.chat_url,
        json={
            "question": example["question"],
            "question_set_version_id": version["id"],
            "question_example_id": example["id"],
            **extra,
        },
    )


def test_reviewed_set_reuse_and_edit_cannot_change_old_run_or_leak_reference(qs):
    draft = create(qs)
    approved = approve(qs, draft)
    assert approved["review_status"] == "approved"
    assert approved["examples"][0]["review"]["reviewed_at"]
    old = qs.client.get(f"{qs.set_url}/versions/{draft['id']}").json()
    assert old["examples"][0]["review"]["status"] == "pending"
    first = execute(qs, approved)
    second = execute(qs, approved, top_k=2)
    assert first.status_code == second.status_code == 200
    messages = str(qs.completion.call_args.kwargs["messages"])
    assert "evaluation-only-reference" not in messages
    assert "evaluation-only-reference" not in str(first.json())
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(first.json()["run_id"]))
        assert str(run.question_set_version_id) == approved["id"]
        assert str(run.question_example_id) == approved["examples"][0]["id"]
        original_snapshot = run.details["question_example_snapshot"]
        assert (
            original_snapshot["example"]["reference_answer"]
            == "evaluation-only-reference"
        )
        assert (
            db.query(ChatHistory).count() == 0
        )  # protected evaluation data isn't copied into public history
    edited = qs.client.patch(
        f"{qs.set_url}/examples/{approved['examples'][0]['id']}",
        json={
            "base_version_id": approved["id"],
            "reference_answer": "edited reference",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["examples"][0]["review"]["status"] == "pending"
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(first.json()["run_id"]))
        assert run.details["question_example_snapshot"] == original_snapshot
    assert execute(qs, approved).status_code == 200


def test_evidence_is_anchored_to_source_text_and_survives_chunking_change(qs):
    version = approve(qs, create(qs))
    evidence = version["examples"][0]["evidence"][0]
    text = pipeline.CONTROL.read_text(encoding="utf-8")
    assert text[evidence["start_char"] : evidence["end_char"]] == evidence["quote"]
    assert evidence["document_version_id"] == qs.document_id
    assert "chunk_id" not in evidence
    assert (
        qs.client.patch(
            qs.chat_url.removesuffix("/chat"),
            json={"chunk_size": 200, "chunk_overlap": 30},
        ).status_code
        == 200
    )
    assert execute(qs, version).status_code == 200
    assert (
        qs.client.get(f"{qs.set_url}/versions/{version['id']}").json()["examples"][0][
            "evidence"
        ][0]
        == evidence
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"quote": "not in the source"},
        {"start_char": 99999},
        {"end_char": 99999},
        {"document_version_id": str(uuid4())},
    ],
)
def test_invalid_evidence_is_rejected_atomically(qs, changes):
    proof = {"document_version_id": qs.document_id, "quote": pipeline.ANSWER, **changes}
    response = qs.client.post(
        qs.sets_url,
        json={"name": "invalid", "examples": [{"question": "q", "evidence": [proof]}]},
    )
    assert response.status_code == 422
    assert qs.client.get(qs.sets_url).json() == []


def test_ambiguous_quote_requires_explicit_source_position(qs):
    uploaded = pipeline.upload(qs, b"same same", "repeat.txt").json()
    proof = {"document_version_id": uploaded["document_version_id"], "quote": "same"}
    payload = {"name": "repeated", "examples": [{"question": "q", "evidence": [proof]}]}
    assert qs.client.post(qs.sets_url, json=payload).status_code == 422
    proof["start_char"] = 5
    response = qs.client.post(qs.sets_url, json=payload)
    assert response.status_code == 201
    assert response.json()["version"]["examples"][0]["evidence"][0]["end_char"] == 9


@pytest.mark.parametrize("format", ["json", "jsonl", "csv"])
def test_import_formats_and_tuning_test_filter_preserve_unreviewed_status(qs, format):
    base = create(qs, [])
    rows = [
        {
            "question": "Question one?",
            "reference_answer": "answer",
            "category": "facts",
            "split": "tuning",
        },
        {"question": "Question two?", "unanswerable": True, "split": "test"},
    ]
    if format == "json":
        content = json.dumps({"schema_version": 1, "examples": rows})
    elif format == "jsonl":
        content = "\n".join(json.dumps(row) for row in rows)
    else:
        stream = io.StringIO()
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "question",
                "reference_answer",
                "category",
                "unanswerable",
                "split",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
        content = stream.getvalue()
    response = qs.client.post(
        f"{qs.set_url}/import",
        params={"base_version_id": base["id"]},
        files={
            "file": (
                f"questions.{format}",
                content.encode("utf-8-sig"),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 201, response.text
    version = response.json()
    assert len(version["examples"]) == 2
    assert all(ex["review"]["status"] == "pending" for ex in version["examples"])
    assert version["examples"][1]["reference_answer"] is None
    assert version["examples"][1]["unanswerable"] is True
    selected = qs.client.get(
        f"{qs.set_url}/versions/{version['id']}", params={"split": "test"}
    ).json()
    assert len(selected["examples"]) == 1
    assert selected["sha256"] == version["sha256"]


@pytest.mark.parametrize(
    "rows",
    [
        [{"question": "good"}, {"question": " "}],
        [
            {"question": "same", "split": "tuning"},
            {"question": " SAME ", "split": "test"},
        ],
        [{"question": "q", "review": {"status": "approved"}}],
        [{"question": "q", "review_status": "approved"}],
    ],
)
def test_invalid_or_falsely_reviewed_import_never_partially_changes_set(qs, rows):
    base = create(qs, [])
    response = qs.client.post(
        f"{qs.set_url}/import",
        params={"base_version_id": base["id"]},
        files={"file": ("q.json", json.dumps(rows).encode(), "application/json")},
    )
    assert response.status_code == 422
    assert len(qs.client.get(f"{qs.set_url}/versions").json()) == 1


def test_edit_conflict_delete_and_immutable_version(qs):
    original = create(qs)
    checked = approve(qs, original)
    example_id = original["examples"][0]["id"]
    conflict = qs.client.patch(
        f"{qs.set_url}/examples/{example_id}",
        json={"base_version_id": original["id"], "category": "new"},
    )
    assert conflict.status_code == 409
    deleted = qs.client.delete(
        f"{qs.set_url}/examples/{example_id}", params={"base_version_id": checked["id"]}
    )
    assert deleted.status_code == 200
    assert deleted.json()["examples"] == []
    assert (
        len(qs.client.get(f"{qs.set_url}/versions/{checked['id']}").json()["examples"])
        == 1
    )
    with TestingSessionLocal() as db:
        version = db.get(QuestionSetVersion, UUID(checked["id"]))
        version.examples = []
        with pytest.raises(ValueError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("state", ["pending", "rejected"])
def test_only_human_approved_examples_can_be_bound_to_runs(qs, state):
    version = create(qs)
    if state == "rejected":
        version = approve(qs, version, status="rejected")
    assert execute(qs, version).status_code == 422
    qs.completion.assert_not_called()


def test_changed_corpus_requires_new_set_version_and_review(qs):
    approved = approve(qs, create(qs))
    assert (
        pipeline.upload(qs, b"replacement document", pipeline.CONTROL.name).json()[
            "status"
        ]
        == "completed"
    )
    assert execute(qs, approved).status_code == 409
    qs.completion.assert_not_called()
    with TestingSessionLocal() as db:
        assert db.query(ExecutionRun).count() == 0
    example = {
        "id": approved["examples"][0]["id"],
        "question": pipeline.QUESTION,
        "split": "test",
    }
    updated = qs.client.post(
        f"{qs.set_url}/versions",
        json={
            "base_version_id": approved["id"],
            "refresh_documents": True,
            "examples": [example],
        },
    )
    assert updated.status_code == 201
    assert updated.json()["documents_sha256"] != approved["documents_sha256"]
    assert updated.json()["examples"][0]["review"]["status"] == "pending"


def test_question_sets_and_bound_runs_work_without_passwords(qs):
    approved = approve(qs, create(qs))
    for path in (
        qs.sets_url,
        f"{qs.set_url}/versions",
        f"{qs.set_url}/versions/{approved['id']}",
        f"{qs.set_url}/versions/{approved['id']}/export",
    ):
        assert qs.client.get(path).status_code == 200
    assert execute(qs, approved).status_code == 200
    assert qs.client.post(qs.sets_url, json={"name": "another set"}).status_code == 201


def test_question_set_api_does_not_require_authorization_headers(qs):
    schema = qs.client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        if (
            "question-sets" in path
            or path.endswith("/chat")
            or path.endswith("/chat/generate")
        ):
            for operation in operations.values():
                if isinstance(operation, dict):
                    assert not operation.get("security")
                    assert not [
                        parameter
                        for parameter in operation.get("parameters", [])
                        if parameter.get("in") == "header"
                    ]


def test_export_reimport_keeps_questions_but_does_not_transfer_human_approval(qs):
    approved = approve(qs, create(qs))
    exported = qs.client.get(f"{qs.set_url}/versions/{approved['id']}/export").json()
    other = create(qs, [])
    response = qs.client.post(
        f"{qs.set_url}/import",
        params={"base_version_id": other["id"]},
        files={
            "file": ("export.json", json.dumps(exported).encode(), "application/json")
        },
    )
    assert response.status_code == 201
    assert response.json()["examples"][0]["review"]["status"] == "pending"


def test_unanswerable_example_and_generation_only_run_are_pinned(qs):
    version = approve(
        qs,
        create(
            qs,
            [
                {
                    "question": "Missing information?",
                    "unanswerable": True,
                    "split": "test",
                }
            ],
        ),
    )
    example = version["examples"][0]
    response = qs.client.post(
        f"{qs.chat_url}/generate",
        json={
            "query": example["question"],
            "context": "provided context",
            "question_set_version_id": version["id"],
            "question_example_id": example["id"],
        },
    )
    assert response.status_code == 200
    with TestingSessionLocal() as db:
        run = db.get(ExecutionRun, UUID(response.json()["run_id"]))
        assert (
            run.details["question_example_snapshot"]["example"]["unanswerable"] is True
        )
        assert run.question_set_version_id == UUID(version["id"])


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"question": "different question"}, 422),
        ({"question_example_id": str(uuid4())}, 404),
        ({"question_set_version_id": str(uuid4())}, 404),
        ({"question_example_id": None}, 422),
        ({"session_id": str(uuid4())}, 422),
    ],
)
def test_bound_execution_rejects_wrong_question_identity_or_dialogue(
    qs, change, expected
):
    checked = approve(qs, create(qs))
    assert execute(qs, checked, **change).status_code == expected
    qs.completion.assert_not_called()


def test_credentials_and_foreign_document_links_cannot_enter_set(qs):
    foreign = qs.client.post(
        f"{pipeline.BASE}/projects/",
        json={"name": "other", "embedding_model": "control", "llm_model": "control"},
    ).json()
    foreign_url = f"{pipeline.BASE}/projects/{foreign['id']}/question-sets"
    response = qs.client.post(
        foreign_url,
        json={
            "name": "bad",
            "examples": [
                {
                    "question": "q",
                    "evidence": [
                        {
                            "document_version_id": qs.document_id,
                            "quote": pipeline.ANSWER,
                        }
                    ],
                }
            ],
        },
    )
    assert response.status_code == 422
    secret = "private-test-llm-key"
    assert (
        qs.client.patch(
            qs.chat_url.removesuffix("/chat"), json={"llm_api_key": secret}
        ).status_code
        == 200
    )
    response = qs.client.post(
        qs.sets_url,
        json={"name": "bad", "examples": [{"question": f"question {secret}"}]},
    )
    assert response.status_code == 422


def test_unmodified_checked_examples_keep_review_when_adding_new_question(qs):
    checked = approve(qs, create(qs))
    response = qs.client.post(
        f"{qs.set_url}/examples",
        json={
            "base_version_id": checked["id"],
            "example": {"question": "Another question?", "split": "tuning"},
        },
    )
    assert response.status_code == 201
    assert response.json()["examples"][0]["review"]["status"] == "approved"
    assert response.json()["examples"][1]["review"]["status"] == "pending"
