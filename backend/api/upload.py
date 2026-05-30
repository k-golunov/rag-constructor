import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from backend.core.ingestion.parser import (
    SUPPORTED_EXTENSIONS,
    EmptyPDFError,
    ParserError,
    get_parser_for,
)
from backend.core.ingestion.pipeline import process_archive, process_file
from backend.db.database import get_db
from backend.db.models import Project

router = APIRouter(prefix="/upload", tags=["upload"])

_OPERATIONS: Dict[str, Dict[str, Any]] = {}

_FORMATS_HINT = ", ".join(SUPPORTED_EXTENSIONS)


def get_project(project_id: UUID, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден",
        )
    return project


@router.post(
    "/single",
    status_code=status.HTTP_200_OK,
    summary=f"Загрузить один файл ({_FORMATS_HINT}) и получить чанки",
)
async def upload_single(
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    project = get_project(project_id, db)

    filename = file.filename or "unknown"
    if get_parser_for(filename) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Неподдерживаемое расширение файла: {filename}. "
                f"Допустимые форматы: {_FORMATS_HINT}"
            ),
        )

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(await file.read())

    try:
        chunks = process_file(
            tmp_path, filename, project.chunk_size, project.chunk_overlap
        )
    except EmptyPDFError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except ParserError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "filename": filename,
        "project_id": str(project_id),
        "chunk_size": project.chunk_size,
        "chunk_overlap": project.chunk_overlap,
        "chunks_count": len(chunks),
        "chunks": [c.model_dump() for c in chunks],
    }


@router.post(
    "/archive",
    status_code=status.HTTP_202_ACCEPTED,
    summary=f"Загрузить ZIP-архив (внутри допустимы: {_FORMATS_HINT}) для фоновой обработки",
)
async def upload_archive(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    project = get_project(project_id, db)

    filename = file.filename or "archive.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ожидается ZIP-архив (.zip)",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="rag_upload_"))
    archive_path = tmp_dir / filename
    archive_path.write_bytes(await file.read())

    if not zipfile.is_zipfile(archive_path):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Повреждённый или некорректный ZIP-архив",
        )

    operation_id = str(uuid.uuid4())
    _OPERATIONS[operation_id] = {"status": "processing", "result": None, "error": None}

    background_tasks.add_task(
        process_archive,
        archive_path,
        tmp_dir,
        project.chunk_size,
        project.chunk_overlap,
        _OPERATIONS,
        operation_id,
    )

    return {"operation_id": operation_id, "message": "Обработка архива начата"}


@router.get(
    "/status/{operation_id}",
    status_code=status.HTTP_200_OK,
    summary="Статус фоновой операции обработки архива",
)
async def get_operation_status(operation_id: str) -> Dict[str, Any]:
    op = _OPERATIONS.get(operation_id)
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Операция {operation_id} не найдена",
        )
    return {"operation_id": operation_id, **op}
