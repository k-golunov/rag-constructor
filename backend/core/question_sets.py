"""Immutable question sets with evidence anchored to source documents."""

import copy
import hashlib
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from backend.config import settings
from backend.core.ingestion.parser import get_parser_for
from backend.core.response_trace import redact
from backend.core.versioning import document_manifest, fingerprint, lock_project
from backend.db.models import DocumentVersion, Project, QuestionSet, QuestionSetVersion
from backend.db.question_set_schemas import ExampleInput


def project_or_404(db, project_id):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Проект не найден.")
    return project


def set_or_404(db, project_id, set_id, lock=False):
    query = db.query(QuestionSet).filter_by(id=set_id, project_id=project_id)
    dataset = (query.with_for_update() if lock else query).first()
    if dataset is None:
        raise HTTPException(404, "Набор вопросов не найден.")
    return dataset


def latest_version(db, dataset):
    return (
        db.query(QuestionSetVersion)
        .filter_by(question_set_id=dataset.id)
        .order_by(QuestionSetVersion.number.desc())
        .first()
    )


def editable_version(db, project_id, set_id, base_id):
    dataset = set_or_404(db, project_id, set_id, lock=True)
    current = latest_version(db, dataset)
    if current.id != base_id:
        raise HTTPException(
            409,
            "Набор уже изменён. Загрузите последнюю версию и повторите редактирование.",
        )
    return dataset, current


def check_no_credentials(value, project):
    secrets = (
        project.llm_api_key,
        project.embedding_api_key,
        settings.OPENAI_API_KEY,
        settings.QDRANT_API_KEY,
        os.getenv("OPENAI_API_KEY"),
    )
    if redact(value, secrets) != value:
        raise HTTPException(
            422,
            "Вопросы или подтверждения содержат API-ключ либо credentials. Удалите их перед сохранением.",
        )


def _evidence(db, project_id, evidence, scope, text_cache):
    identifier = str(evidence.document_version_id)
    if identifier not in scope:
        raise HTTPException(
            422,
            "Подтверждение должно ссылаться на документ из состава этой версии набора.",
        )
    version = db.get(DocumentVersion, evidence.document_version_id)
    if version is None or version.project_id != project_id:
        raise HTTPException(422, "Версия исходного документа не найдена в проекте.")
    if identifier not in text_cache:
        path = Path(version.file_path)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != version.sha256:
            raise HTTPException(
                422, "Контрольная сумма исходного документа не совпадает."
            )
        parser = get_parser_for(version.file_name)
        text_cache[identifier] = (parser.parse(str(path)), type(parser).__name__)
    text, parser_name = text_cache[identifier]
    start = evidence.start_char
    if start is None:
        start = text.find(evidence.quote)
        if start >= 0 and text.find(evidence.quote, start + 1) >= 0:
            raise HTTPException(
                422, "Выдержка встречается несколько раз. Укажите start_char."
            )
    end = (
        evidence.end_char
        if evidence.end_char is not None
        else start + len(evidence.quote)
    )
    if not 0 <= start < end <= len(text) or text[start:end] != evidence.quote:
        raise HTTPException(
            422, "Выдержка и её позиции не совпадают с текстом исходного документа."
        )
    return {
        "document_version_id": identifier,
        "document_sha256": version.sha256,
        "file_name": version.file_name,
        "quote": evidence.quote,
        "start_char": start,
        "end_char": end,
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "parser": parser_name,
    }


def normalize_examples(
    db, project, examples, documents, previous=(), reset_reviews=False
):
    scope = {item["document_version_id"] for item in documents}
    old = {item["id"]: item for item in previous}
    seen_ids, seen_questions, result, text_cache = set(), set(), [], {}
    for example in examples:
        value = example.model_dump(mode="json")
        check_no_credentials(value, project)
        identity = str(example.id)
        normalized_question = " ".join(example.question.casefold().split())
        if identity in seen_ids or normalized_question in seen_questions:
            raise HTTPException(
                422,
                "В наборе повторяется идентификатор или текст вопроса, в том числе между tuning и test.",
            )
        seen_ids.add(identity)
        seen_questions.add(normalized_question)
        value["evidence"] = [
            _evidence(db, project.id, item, scope, text_cache)
            for item in example.evidence
        ]
        value["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
        existing = old.get(identity)
        if (
            not reset_reviews
            and existing is not None
            and {k: v for k, v in existing.items() if k != "review"}
            == {k: v for k, v in value.items() if k != "review"}
        ):
            value["review"] = copy.deepcopy(existing["review"])
        result.append(value)
    return result


def example_input(example):
    value = {
        key: copy.deepcopy(value) for key, value in example.items() if key != "review"
    }
    value["evidence"] = [
        {
            key: evidence[key]
            for key in ("document_version_id", "quote", "start_char", "end_char")
        }
        for evidence in value["evidence"]
    ]
    return ExampleInput.model_validate(value)


def create_version(db, dataset, examples, documents, number):
    snapshot = copy.deepcopy({"examples": examples, "documents": documents})
    version = QuestionSetVersion(
        question_set_id=dataset.id,
        number=number,
        sha256=fingerprint(snapshot),
        documents=snapshot["documents"],
        documents_sha256=fingerprint(documents),
        examples=snapshot["examples"],
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def current_documents(db, project_id):
    lock_project(db, project_id)
    try:
        return document_manifest(db, project_id)
    except ValueError as exc:
        logging.getLogger(__name__).exception(
            "Не удалось проверить версии документов проекта %s", project_id
        )
        raise HTTPException(
            422, "Не удалось проверить версии документов. Загрузите источники повторно."
        ) from exc


def version_result(version):
    return {
        "id": str(version.id),
        "question_set_id": str(version.question_set_id),
        "number": version.number,
        "sha256": version.sha256,
        "documents": version.documents,
        "documents_sha256": version.documents_sha256,
        "examples": version.examples,
        "created_at": version.created_at,
        "review_status": (
            "approved"
            if version.examples
            and all(item["review"]["status"] == "approved" for item in version.examples)
            else "pending"
        ),
    }


def evaluation_example(db, project_id, version_id, example_id, question):
    if (version_id is None) != (example_id is None):
        raise HTTPException(
            422, "Укажите вместе question_set_version_id и question_example_id."
        )
    if version_id is None:
        return None
    version = db.get(QuestionSetVersion, version_id)
    if version is None:
        raise HTTPException(404, "Версия набора вопросов не найдена.")
    set_or_404(db, project_id, version.question_set_id)
    example = next(
        (item for item in version.examples if item["id"] == str(example_id)), None
    )
    if example is None:
        raise HTTPException(404, "Пример не найден в указанной версии.")
    if example["review"]["status"] != "approved":
        raise HTTPException(
            422, "Для анализа используйте пример, проверенный человеком."
        )
    if example["question"] != question:
        raise HTTPException(422, "Вопрос запроса не совпадает с примером набора.")
    return {
        "question_set_id": str(version.question_set_id),
        "version_id": str(version.id),
        "version_sha256": version.sha256,
        "documents_sha256": version.documents_sha256,
        "example": copy.deepcopy(example),
    }
