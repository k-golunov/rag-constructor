"""Create an independent project with the bundled MkDocs corpus and questions."""

import json
import logging
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.config import settings
from backend.core.question_sets import (
    current_documents,
    normalize_examples,
    stage_version,
)
from backend.core.versioning import register_document
from backend.db.database import get_db
from backend.db.models import Project, QuestionSet
from backend.db.question_set_schemas import ExampleInput
from backend.db.schemas import ProjectCreate, ProjectResponse

router = APIRouter(prefix="/api/v1/public/provider/projects", tags=["projects"])
logger = logging.getLogger(__name__)
DEMO_DIR = Path(__file__).resolve().parents[1] / "datasets" / "mkdocs_demo"
QUESTIONS_PATH = DEMO_DIR / "questions.json"


def _examples_by_version(documents):
    version_ids = {row["file_name"]: row["document_version_id"] for row in documents}
    bundle = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    if bundle["schema_version"] != 1 or not bundle["examples"]:
        raise ValueError("Неизвестный формат или пустой набор MkDocs.")
    examples = []
    for row in bundle["examples"]:
        explanation = row.pop("explanation", None)
        if explanation:
            row["reference_answer"] = f"{row['reference_answer']} {explanation}"
        for evidence in row["evidence"]:
            evidence["document_version_id"] = version_ids[evidence.pop("file_name")]
        examples.append(ExampleInput.model_validate(row))
    return examples


@router.post(
    "/demo/mkdocs",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать проект из демонабора MkDocs",
)
def create_mkdocs_demo(
    payload: ProjectCreate, db: Session = Depends(get_db)
) -> Project:
    project_id = uuid4()
    project = Project(id=project_id, **payload.model_dump())
    try:
        db.add(project)
        db.flush()
        for path in sorted((DEMO_DIR / "corpus").glob("*.txt")):
            source = register_document(db, project, path.name, path.read_bytes())
            source.status = "pending"
        db.flush()
        documents = current_documents(db, project_id)
        examples = normalize_examples(
            db, project, _examples_by_version(documents), documents
        )
        dataset = QuestionSet(
            project_id=project_id,
            name="Документация MkDocs 1.6.1",
            description="Вопросы к документации MkDocs с эталонами и выдержками.",
        )
        db.add(dataset)
        db.flush()
        stage_version(db, dataset, examples, documents, number=1)
        db.commit()
    except Exception:
        db.rollback()
        rmtree(Path(settings.UPLOAD_DIR) / str(project_id), ignore_errors=True)
        logger.exception("Не удалось создать демопроект MkDocs %s", project_id)
        raise HTTPException(500, "Не удалось загрузить демонабор MkDocs.") from None

    return project
