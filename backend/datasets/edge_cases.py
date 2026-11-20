import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from backend.core.evaluators import checksum
from backend.db.metric_schemas import Evidence, MetricInput, Provenance, RetrievedChunk
from backend.db.question_set_schemas import ExampleInput

KIT_DIR = Path(__file__).resolve().parent / "rag_edge_cases"
CORPUS_DIR = KIT_DIR / "corpus"
QUESTIONS_PATH = KIT_DIR / "questions.json"
FIXTURES_PATH = KIT_DIR / "evaluator_fixtures.json"

CATEGORIES = (
    "Перефразирование",
    "Похожие документы",
    "Несколько выдержек",
    "Отвлекающий текст",
    "Ошибочная предпосылка",
    "Нет ответа",
)

FIXTURE_NAMESPACE = uuid5(
    NAMESPACE_URL, "rag-constructor/edge-cases/evaluator-fixtures"
)


class Excerpt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_name: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correct: bool
    grounded: bool
    complete: bool


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    kind: Literal["correct", "incomplete", "hallucinated", "wrong_document"]
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    context: list[Excerpt] = Field(min_length=1)
    expected: Expectation
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class FixtureSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    synthetic: Literal[True]
    fixtures: list[Fixture] = Field(min_length=1)


def corpus_files():
    return sorted(CORPUS_DIR.glob("*.txt"))


@cache
def corpus_text(file_name):
    path = CORPUS_DIR / file_name
    if path.parent != CORPUS_DIR or not path.is_file():
        raise ValueError(f"В корпусе нет файла {file_name}.")
    return path.read_text(encoding="utf-8-sig")


@cache
def _file_sha256(file_name):
    return hashlib.sha256((CORPUS_DIR / file_name).read_bytes()).hexdigest()


def _fake_id(name):
    return uuid5(FIXTURE_NAMESPACE, name)


def _document_id(file_name):
    return _fake_id("document:" + _file_sha256(file_name))


def load_examples():
    payload = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    return [ExampleInput.model_validate(row) for row in payload["examples"]]


def load_fixtures():
    return FixtureSet.model_validate_json(FIXTURES_PATH.read_text(encoding="utf-8"))


def locate(file_name, quote):
    text = corpus_text(file_name)
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise ValueError(
            f"Выдержка встречается в {file_name} не ровно один раз: {quote!r}."
        )
    return start, start + len(quote)


def _evidence(file_name, quote):
    start, end = locate(file_name, quote)
    return Evidence(
        document_version_id=_document_id(file_name),
        document_sha256=_file_sha256(file_name),
        file_name=file_name,
        quote=quote,
        start_char=start,
        end_char=end,
        source_text_sha256=hashlib.sha256(
            corpus_text(file_name).encode("utf-8")
        ).hexdigest(),
        parser="TextParser",
    )


def _metric_input(fixture, example, set_sha256, corpus_sha256):
    chunks = [
        RetrievedChunk(
            id=f"{fixture.id}-{rank}",
            rank=rank,
            text=excerpt.quote,
            source=excerpt.file_name,
            document_version_id=_document_id(excerpt.file_name),
            document_sha256=_file_sha256(excerpt.file_name),
        )
        for rank, excerpt in enumerate(fixture.context, start=1)
    ]
    return MetricInput(
        question=fixture.question,
        answer=fixture.answer,
        context="\n\n".join(excerpt.quote for excerpt in fixture.context),
        reference_answer=example.reference_answer,
        evidence=[_evidence(item.file_name, item.quote) for item in example.evidence],
        retrieved_chunks=chunks,
        context_chunk_ids=[chunk.id for chunk in chunks],
        unanswerable=example.unanswerable,
        category=example.category,
        processing_status="success",
        retrieval_status="success",
        provenance=Provenance(
            project_id=FIXTURE_NAMESPACE,
            batch_id=_fake_id("batch"),
            variant_id=_fake_id("variant"),
            result_id=_fake_id("result:" + fixture.id),
            question_example_id=example.id,
            repeat_number=1,
            execution_run_id=_fake_id("run:" + fixture.id),
            configuration_version_id=_fake_id("configuration"),
            index_version_id=_fake_id("index"),
            documents_sha256=corpus_sha256,
            question_set_version_id=_fake_id("questions:" + set_sha256),
            question_set_sha256=set_sha256,
            source_trace_sha256=checksum(fixture.model_dump(mode="json")),
            source_trace_schema_version=1,
            question_isolated=True,
        ),
    )


def fixture_samples():
    examples = {example.question: example for example in load_examples()}
    set_sha256 = hashlib.sha256(QUESTIONS_PATH.read_bytes()).hexdigest()
    corpus_sha256 = checksum(
        {path.name: _file_sha256(path.name) for path in corpus_files()}
    )
    samples = []
    for fixture in load_fixtures().fixtures:
        example = examples.get(fixture.question)
        if example is None:
            raise ValueError(f"Вопроса для тестового ответа {fixture.id} нет в наборе.")
        samples.append(
            (fixture, _metric_input(fixture, example, set_sha256, corpus_sha256))
        )
    return samples
