import asyncio
import csv
import io
import json
import re
from collections import Counter
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.core.evaluators import TransportStub, evaluate_saved_input
from backend.core.question_sets import document_lookup, resolve_document
from backend.datasets import edge_cases as kit
from backend.db.question_set_schemas import EvidenceInput
from backend.tests import test_question_sets as sets
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
qs = sets.qs

EXAMPLES = kit.load_examples()
FIXTURES = kit.load_fixtures()
CORPUS = {path.name: kit.corpus_text(path.name) for path in kit.corpus_files()}
KINDS = {"correct", "incomplete", "hallucinated", "wrong_document"}


def in_category(name):
    return [example for example in EXAMPLES if example.category == name]


def pairs(excerpts):
    return {(item.file_name, item.quote) for item in excerpts}


def numbers(text):
    return set(re.findall(r"\d+(?: \d{3})*", text))


def test_kit_covers_every_category_in_both_splits():
    assert 60 <= len(EXAMPLES) <= 80
    counts = Counter(example.category for example in EXAMPLES)
    assert set(counts) == set(kit.CATEGORIES)
    assert min(counts.values()) >= 10
    for name in kit.CATEGORIES:
        assert {example.split for example in in_category(name)} == {"tuning", "test"}
    normalized = [" ".join(example.question.casefold().split()) for example in EXAMPLES]
    assert len(set(normalized)) == len(normalized)


def test_every_case_has_an_explanation():
    for example in EXAMPLES:
        assert example.explanation, example.question


def test_answerable_cases_have_reference_and_unique_quotes():
    for example in EXAMPLES:
        if example.category == "Нет ответа":
            assert example.unanswerable
            assert example.reference_answer is None and example.evidence == []
            continue
        assert not example.unanswerable and example.reference_answer
        assert example.evidence, example.question
        for proof in example.evidence:
            assert CORPUS[proof.file_name].count(proof.quote) == 1, proof.quote
    for example in in_category("Несколько выдержек"):
        assert 2 <= len(example.evidence) <= 3


def test_reference_numbers_come_from_evidence_unless_computed():
    computed = {"33", "37", "16 000", "3 600", "12"}
    for example in EXAMPLES:
        if example.reference_answer is None:
            continue
        known = numbers(example.question)
        known |= numbers(" ".join(proof.quote for proof in example.evidence))
        assert numbers(example.reference_answer) - known <= computed, example.question


def test_correct_fixture_numbers_are_backed_by_context_or_reference():
    cases = {example.question: example for example in EXAMPLES}
    for fixture in FIXTURES.fixtures:
        if fixture.kind != "correct":
            continue
        backed = numbers(" ".join(item.quote for item in fixture.context))
        backed |= numbers(cases[fixture.question].reference_answer or "")
        assert numbers(fixture.answer) <= backed, fixture.id


def test_fixtures_are_labelled_synthetic_and_cover_every_kind():
    assert FIXTURES.synthetic is True
    assert {fixture.kind for fixture in FIXTURES.fixtures} == KINDS
    ids = [fixture.id for fixture in FIXTURES.fixtures]
    assert len(set(ids)) == len(ids)


def test_fixture_expectations_follow_from_kind():
    for fixture in FIXTURES.fixtures:
        expected = fixture.expected
        assert expected.correct == (fixture.kind == "correct"), fixture.id
        if fixture.kind == "correct":
            assert expected.grounded and expected.complete
        elif fixture.kind == "incomplete":
            assert expected.grounded and not expected.complete
            assert fixture.missing_facts
        elif fixture.kind == "hallucinated":
            assert not expected.grounded and fixture.unsupported_claims
        else:
            assert not expected.complete


def test_fixture_claims_can_be_checked_against_the_corpus():
    cases = {example.question: example for example in EXAMPLES}
    everything = "\n".join(CORPUS.values())
    for fixture in FIXTURES.fixtures:
        case = cases[fixture.question]
        for excerpt in fixture.context:
            assert CORPUS[excerpt.file_name].count(excerpt.quote) == 1, fixture.id
        for claim in fixture.unsupported_claims:
            assert claim in fixture.answer and claim not in everything, claim
        known = (case.reference_answer or "") + " ".join(
            proof.quote for proof in case.evidence
        )
        for fact in fixture.missing_facts:
            assert fact not in fixture.answer and fact in known, fact
        context, evidence = pairs(fixture.context), pairs(case.evidence)
        if fixture.kind == "wrong_document":
            assert {name for name, _ in context} - {name for name, _ in evidence}
            assert evidence - context
        else:
            assert evidence <= context, fixture.id


def test_fixture_samples_pass_the_metric_contract():
    samples = kit.fixture_samples()
    assert len(samples) == len(FIXTURES.fixtures)
    ids = {example.question: example.id for example in EXAMPLES}
    stub = TransportStub()
    for fixture, data in samples:
        assert data.provenance.project_id == kit.FIXTURE_NAMESPACE
        assert data.provenance.question_example_id == ids[fixture.question]
        assert data.context == "\n\n".join(item.quote for item in fixture.context)
        assert data.context_chunk_ids == [chunk.id for chunk in data.retrieved_chunks]
        for proof in data.evidence:
            text = CORPUS[proof.file_name]
            assert text[proof.start_char : proof.end_char] == proof.quote
        result = asyncio.run(evaluate_saved_input(data, stub, {}))
        assert result.status == "validated", fixture.id


def test_document_lookup_by_name():
    ids = [str(uuid4()) for _ in range(5)]
    documents = [
        {"document_version_id": ids[0], "file_name": "corpus/notes.txt"},
        {"document_version_id": ids[1], "file_name": "notes.txt"},
        {"document_version_id": ids[2], "file_name": "a/report.txt"},
        {"document_version_id": ids[3], "file_name": "b/report.txt"},
        {"document_version_id": ids[4], "file_name": "plan.txt"},
    ]

    lookup = document_lookup(documents)

    def resolve(**fields):
        return resolve_document(EvidenceInput(quote="q", **fields), lookup)

    assert resolve(file_name="notes.txt") == ids[1]
    assert resolve(file_name="corpus/notes.txt") == ids[0]
    assert resolve(file_name="old/plan.txt") == ids[4]
    assert resolve(file_name="old\\plan.txt") == ids[4]
    assert resolve(document_version_id=ids[2], file_name="report.txt") == ids[2]
    for fields, message in (
        ({"file_name": "report.txt"}, "несколько документов"),
        ({"file_name": "missing.txt"}, "нет в составе"),
        ({"document_version_id": ids[2], "file_name": "notes.txt"}, "разные документы"),
        ({"document_version_id": str(uuid4()), "file_name": "notes.txt"}, "состава"),
    ):
        with pytest.raises(HTTPException) as error:
            resolve(**fields)
        assert error.value.status_code == 422
        assert message in error.value.detail
    with pytest.raises(ValidationError):
        EvidenceInput(quote="q")


@pytest.fixture
def kit_project(rag):
    rag.embedder.embed.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    for path in kit.corpus_files():
        response = pipeline.upload(rag, path.read_bytes(), path.name)
        assert response.json()["status"] == "completed", response.text
    rag.sets_url = f"{pipeline.BASE}/projects/{rag.project_id}/question-sets"
    return rag


def test_bundled_questions_import_into_a_project_with_the_corpus(kit_project):
    client = kit_project.client
    created = client.post(kit_project.sets_url, json={"name": "Сложные случаи RAG"})
    assert created.status_code == 201, created.text
    set_url = f"{kit_project.sets_url}/{created.json()['id']}"
    response = client.post(
        f"{set_url}/import",
        params={"base_version_id": created.json()["version"]["id"]},
        files={"file": ("questions.json", kit.QUESTIONS_PATH.read_bytes())},
    )
    assert response.status_code == 201, response.text
    version = response.json()
    documents = {
        item["file_name"]: item["document_version_id"] for item in version["documents"]
    }
    assert set(documents) == set(CORPUS)
    assert len(version["examples"]) == len(EXAMPLES)
    for stored, source in zip(version["examples"], EXAMPLES, strict=True):
        assert stored["explanation"] == source.explanation
        assert stored["category"] == source.category
        assert stored["review"]["status"] == "pending"
        for saved, proof in zip(stored["evidence"], source.evidence, strict=True):
            assert saved["document_version_id"] == documents[proof.file_name]
            assert saved["file_name"] == proof.file_name
            text = CORPUS[proof.file_name]
            assert text[saved["start_char"] : saved["end_char"]] == proof.quote
    exported = client.get(f"{set_url}/versions/{version['id']}/export").json()
    assert [row["explanation"] for row in exported["examples"]] == [
        example.explanation for example in EXAMPLES
    ]


def test_explanation_is_optional_and_does_not_reset_other_reviews(qs):
    version = sets.create(
        qs,
        [
            {"question": "Первый вопрос?", "split": "test"},
            {"question": "Второй вопрос?", "split": "test"},
        ],
    )
    for example in version["examples"]:
        assert "explanation" not in example
        version = sets.approve(qs, version, example["id"])
    first, second = version["examples"]
    edited = qs.client.patch(
        f"{qs.set_url}/examples/{first['id']}",
        json={"base_version_id": version["id"], "explanation": "Ловит пропуск даты."},
    )
    assert edited.status_code == 200, edited.text
    first, second = edited.json()["examples"]
    assert first["explanation"] == "Ловит пропуск даты."
    assert first["review"]["status"] == "pending"
    assert second["review"]["status"] == "approved"
    assert "explanation" not in second
    cleared = qs.client.patch(
        f"{qs.set_url}/examples/{first['id']}",
        json={"base_version_id": edited.json()["id"], "explanation": "   "},
    )
    assert cleared.status_code == 200, cleared.text
    assert "explanation" not in cleared.json()["examples"][0]
    too_long = qs.client.patch(
        f"{qs.set_url}/examples/{first['id']}",
        json={"base_version_id": cleared.json()["id"], "explanation": "x" * 2001},
    )
    assert too_long.status_code == 422


def test_csv_import_accepts_explanation_and_file_name_evidence(qs):
    base = sets.create(qs, [])
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["question", "evidence", "explanation"])
    writer.writeheader()
    writer.writerow(
        {
            "question": pipeline.QUESTION,
            "evidence": json.dumps(
                [{"file_name": pipeline.CONTROL.name, "quote": pipeline.ANSWER}]
            ),
            "explanation": "Проверяет точный код доступа.",
        }
    )
    response = qs.client.post(
        f"{qs.set_url}/import",
        params={"base_version_id": base["id"]},
        files={"file": ("questions.csv", stream.getvalue().encode("utf-8"))},
    )
    assert response.status_code == 201, response.text
    example = response.json()["examples"][0]
    assert example["explanation"] == "Проверяет точный код доступа."
    assert example["evidence"][0]["document_version_id"] == qs.document_id
