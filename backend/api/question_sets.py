"""Manual authoring, review and atomic import of question sets."""

import copy
import csv
import io
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.core.question_sets import (
    check_no_credentials,
    project_or_404,
    set_or_404,
    latest_version,
    editable_version,
    normalize_examples,
    example_input,
    create_version,
    current_documents,
    version_result,
)
from backend.core.ingestion.parser import ParserError
from backend.db.database import get_db
from backend.db.models import QuestionSet, QuestionSetVersion
from backend.db.question_set_schemas import (
    SetCreate,
    VersionCreate,
    ExampleCreate,
    ExampleEdit,
    ExampleReview,
    ExampleInput,
)

router = APIRouter(
    prefix="/api/v1/public/provider/projects/{project_id}/question-sets",
    tags=["question-sets"],
)


def _normalize(db, project, inputs, documents, previous=(), reset=False):
    if len(inputs) > 10000:
        raise HTTPException(422, "Набор не может содержать более 10000 примеров.")
    try:
        return normalize_examples(db, project, inputs, documents, previous, reset)
    except (OSError, ValueError, ParserError) as exc:
        raise HTTPException(
            422, "Не удалось проверить подтверждения по исходным документам."
        ) from exc


def _version(db, project_id, set_id, version_id):
    set_or_404(db, project_id, set_id)
    version = (
        db.query(QuestionSetVersion)
        .filter_by(id=version_id, question_set_id=set_id)
        .first()
    )
    if version is None:
        raise HTTPException(404, "Версия набора не найдена.")
    return version


@router.post("", status_code=201)
def add_set(project_id: UUID, payload: SetCreate, db: Session = Depends(get_db)):
    project = project_or_404(db, project_id)
    check_no_credentials(
        {"name": payload.name, "description": payload.description}, project
    )
    documents = current_documents(db, project_id)
    examples = _normalize(db, project, payload.examples, documents)
    dataset = QuestionSet(
        project_id=project_id, name=payload.name, description=payload.description
    )
    db.add(dataset)
    db.flush()
    version = create_version(db, dataset, examples, documents, 1)
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "version": version_result(version),
    }


@router.get("")
def list_sets(
    project_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    project_or_404(db, project_id)
    datasets = (
        db.query(QuestionSet)
        .filter_by(project_id=project_id)
        .order_by(QuestionSet.created_at.desc(), QuestionSet.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description,
            "latest_version": {
                key: value
                for key, value in version_result(latest_version(db, item)).items()
                if key not in {"examples", "documents"}
            },
        }
        for item in datasets
    ]


@router.get("/{set_id}/versions")
def list_versions(
    project_id: UUID,
    set_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    set_or_404(db, project_id, set_id)
    versions = (
        db.query(QuestionSetVersion)
        .filter_by(question_set_id=set_id)
        .order_by(QuestionSetVersion.number.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [
        {
            **{
                key: value
                for key, value in version_result(item).items()
                if key not in {"examples", "documents"}
            },
            "examples_count": len(item.examples),
            "approved_count": sum(
                ex["review"]["status"] == "approved" for ex in item.examples
            ),
        }
        for item in versions
    ]


@router.get("/{set_id}/versions/{version_id}")
def get_version(
    project_id: UUID,
    set_id: UUID,
    version_id: UUID,
    split: str | None = Query(None, pattern="^(tuning|test)$"),
    review_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    db: Session = Depends(get_db),
):
    result = version_result(_version(db, project_id, set_id, version_id))
    result["examples"] = [
        ex
        for ex in result["examples"]
        if (split is None or ex["split"] == split)
        and (review_status is None or ex["review"]["status"] == review_status)
    ]
    result["selection"] = {"split": split, "review_status": review_status}
    return result


@router.get("/{set_id}/versions/{version_id}/export")
def export_version(
    project_id: UUID, set_id: UUID, version_id: UUID, db: Session = Depends(get_db)
):
    version = _version(db, project_id, set_id, version_id)
    return {
        "schema_version": 1,
        "examples": [
            example_input(ex).model_dump(mode="json") for ex in version.examples
        ],
    }


@router.post("/{set_id}/versions", status_code=201)
def replace_examples(
    project_id: UUID,
    set_id: UUID,
    payload: VersionCreate,
    db: Session = Depends(get_db),
):
    project = project_or_404(db, project_id)
    dataset, base = editable_version(db, project_id, set_id, payload.base_version_id)
    documents = (
        current_documents(db, project_id)
        if payload.refresh_documents
        else base.documents
    )
    examples = _normalize(
        db,
        project,
        payload.examples,
        documents,
        base.examples,
        payload.refresh_documents,
    )
    return version_result(
        create_version(db, dataset, examples, documents, base.number + 1)
    )


@router.post("/{set_id}/examples", status_code=201)
def add_example(
    project_id: UUID,
    set_id: UUID,
    payload: ExampleCreate,
    db: Session = Depends(get_db),
):
    project = project_or_404(db, project_id)
    dataset, base = editable_version(db, project_id, set_id, payload.base_version_id)
    inputs = [example_input(ex) for ex in base.examples] + [payload.example]
    examples = _normalize(db, project, inputs, base.documents, base.examples)
    return version_result(
        create_version(db, dataset, examples, base.documents, base.number + 1)
    )


@router.patch("/{set_id}/examples/{example_id}")
def edit_example(
    project_id: UUID,
    set_id: UUID,
    example_id: UUID,
    payload: ExampleEdit,
    db: Session = Depends(get_db),
):
    project = project_or_404(db, project_id)
    dataset, base = editable_version(db, project_id, set_id, payload.base_version_id)
    inputs = [example_input(ex) for ex in base.examples]
    target = next((ex for ex in inputs if ex.id == example_id), None)
    if target is None:
        raise HTTPException(404, "Пример не найден.")
    values = target.model_dump(mode="json")
    values.update(
        payload.model_dump(mode="json", exclude_unset=True, exclude={"base_version_id"})
    )
    try:
        updated = ExampleInput.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422, "Некорректные поля примера.") from exc
    inputs = [updated if ex.id == example_id else ex for ex in inputs]
    examples = _normalize(db, project, inputs, base.documents, base.examples)
    return version_result(
        create_version(db, dataset, examples, base.documents, base.number + 1)
    )


@router.delete("/{set_id}/examples/{example_id}")
def delete_example(
    project_id: UUID,
    set_id: UUID,
    example_id: UUID,
    base_version_id: UUID,
    db: Session = Depends(get_db),
):
    dataset, base = editable_version(db, project_id, set_id, base_version_id)
    examples = [
        copy.deepcopy(ex) for ex in base.examples if ex["id"] != str(example_id)
    ]
    if len(examples) == len(base.examples):
        raise HTTPException(404, "Пример не найден.")
    return version_result(
        create_version(db, dataset, examples, base.documents, base.number + 1)
    )


@router.post("/{set_id}/examples/{example_id}/review")
def review_example(
    project_id: UUID,
    set_id: UUID,
    example_id: UUID,
    payload: ExampleReview,
    db: Session = Depends(get_db),
):
    check_no_credentials({"reviewer": payload.reviewer}, project_or_404(db, project_id))
    dataset, base = editable_version(db, project_id, set_id, payload.base_version_id)
    examples = copy.deepcopy(base.examples)
    target = next((ex for ex in examples if ex["id"] == str(example_id)), None)
    if target is None:
        raise HTTPException(404, "Пример не найден.")
    target["review"] = {
        "status": payload.status,
        "reviewer": payload.reviewer if payload.status != "pending" else None,
        "reviewed_at": (
            datetime.now(timezone.utc).isoformat()
            if payload.status != "pending"
            else None
        ),
    }
    return version_result(
        create_version(db, dataset, examples, base.documents, base.number + 1)
    )


def _import_examples(content, filename):
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Файл импорта должен быть не больше 10 МБ.")
    try:
        text = content.decode("utf-8-sig")
        suffix = filename.lower().rsplit(".", 1)[-1]
        if suffix == "csv":
            reader = csv.DictReader(io.StringIO(text))
            allowed = {
                "id",
                "question",
                "reference_answer",
                "evidence",
                "category",
                "unanswerable",
                "split",
            }
            if (
                not reader.fieldnames
                or "question" not in reader.fieldnames
                or len(set(reader.fieldnames)) != len(reader.fieldnames)
                or set(reader.fieldnames) - allowed
            ):
                raise ValueError("Недопустимые столбцы CSV.")
            rows = []
            for row in reader:
                if not any(row.values()):
                    continue
                value = {key: val for key, val in row.items() if val not in (None, "")}
                if "evidence" in value:
                    value["evidence"] = json.loads(value["evidence"])
                if "unanswerable" in value:
                    boolean = value["unanswerable"].strip().lower()
                    if boolean not in {"true", "false", "1", "0"}:
                        raise ValueError("Некорректное логическое поле CSV.")
                    value["unanswerable"] = boolean in {"true", "1"}
                rows.append(value)
        elif suffix == "jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif suffix == "json":
            rows = json.loads(text)
            if isinstance(rows, dict):
                if (
                    set(rows) - {"schema_version", "examples"}
                    or rows.get("schema_version", 1) != 1
                ):
                    raise ValueError("Неподдерживаемый формат импорта.")
                rows = rows["examples"]
        else:
            raise ValueError("Ожидается JSON, JSONL или CSV.")
        if not isinstance(rows, list) or not rows or len(rows) > 10000:
            raise ValueError("В файле должно быть от 1 до 10000 примеров.")
        return [ExampleInput.model_validate(row) for row in rows]
    except (ValueError, KeyError, TypeError, UnicodeError, csv.Error) as exc:
        raise HTTPException(
            422,
            "Некорректный импорт: проверьте UTF-8, поля примеров и формат JSON/JSONL/CSV. Статус проверки задаётся только отдельным действием человека.",
        ) from exc


@router.post("/{set_id}/import", status_code=201)
async def import_examples(
    project_id: UUID,
    set_id: UUID,
    base_version_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    imported = _import_examples(
        await file.read(10 * 1024 * 1024 + 1), file.filename or "questions.json"
    )
    project = project_or_404(db, project_id)
    dataset, base = editable_version(db, project_id, set_id, base_version_id)
    inputs = [example_input(ex) for ex in base.examples] + imported
    examples = _normalize(db, project, inputs, base.documents, base.examples)
    return version_result(
        create_version(db, dataset, examples, base.documents, base.number + 1)
    )
