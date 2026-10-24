import logging
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

from backend.api.projects import validate_project_configuration
from backend.core.factories import build_embedder, build_vector_store
from backend.core.ingestion.parser import (
    SUPPORTED_EXTENSIONS,
    EmptyPDFError,
    ParserError,
    get_parser_for,
)
from backend.core.ingestion.pipeline import (
    process_file,
)
from backend.db.database import get_db, SessionLocal
from backend.db.models import DataSource, Project
from backend.core.versioning import (
    register_document,
    plan_index,
    ensure_index,
    index_snapshot,
    indexing_settings,
    lock_project,
    mark_sources,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/provider/upload", tags=["upload"])

_OPERATIONS: Dict[str, Dict[str, Any]] = {}
_FORMATS_HINT = ", ".join(SUPPORTED_EXTENSIONS)


def _get_project(project_id: UUID, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден",
        )
    validate_project_configuration(project)
    return project


@router.post(
    "/single",
    status_code=status.HTTP_200_OK,
    summary=f"Загрузить один файл ({_FORMATS_HINT}) — парсинг, эмбеддинг, индексация",
)
async def upload_single(
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Загружает файл, разбивает на чанки, встраивает в Qdrant и сохраняет DataSource.

    Args:
        project_id: UUID проекта.
        file: Загружаемый файл.
        db: Сессия БД.

    Returns:
        Информация о файле и количестве обработанных чанков.
    """
    project = _get_project(project_id, db)
    filename = file.filename or "unknown"

    if get_parser_for(filename) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемое расширение: {filename}. Допустимые форматы: {_FORMATS_HINT}",
        )

    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Ожидается имя файла без пути.")
    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp_path.write_bytes(content)
    try:
        process_file(tmp_path, filename, project.chunk_size, project.chunk_overlap)
    except EmptyPDFError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ParserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    spec = indexing_settings(project)
    embedding_key, embedding_url = project.embedding_api_key, project.embedding_api_url
    lock_project(db, project_id)
    ds = register_document(db, project, filename, content)
    uploaded_version_id = ds.document_version_id
    source_id = ds.id
    index = None
    reused = False
    result_status = "failed"
    try:
        index = plan_index(db, project, spec)
        db.commit()
        embedder = build_embedder(
            spec["embedding_model"], api_key=embedding_key, api_base=embedding_url
        )
        reused = not await ensure_index(db, index, build_vector_store(), embedder)
        mark_sources(db, index)
        result_status = "completed"
    except Exception as exc:
        logger.exception("Ошибка индексации %s", filename)
        if ds.document_version_id == uploaded_version_id:
            ds.status = "failed"
            ds.error_message = str(exc)
    db.commit()
    return {
        "filename": filename,
        "project_id": str(project_id),
        "data_source_id": str(source_id),
        "document_version_id": str(uploaded_version_id),
        "status": result_status,
        "chunk_size": spec["chunk_size"],
        "chunk_overlap": spec["chunk_overlap"],
        "chunks_count": (
            index.document_chunks.get(str(uploaded_version_id), 0) if index else 0
        ),
        "index_reused": reused,
        **(index_snapshot(index) if index is not None else {}),
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
    """Принимает ZIP-архив и запускает фоновую обработку (парсинг, эмбеддинг, индексация).

    Args:
        project_id: UUID проекта.
        background_tasks: Очередь фоновых задач FastAPI.
        file: ZIP-архив.
        db: Сессия БД.

    Returns:
        operation_id для отслеживания статуса.
    """
    project = _get_project(project_id, db)
    filename = file.filename or "archive.zip"

    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ожидается ZIP-архив (.zip)",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="rag_upload_"))
    archive_path = tmp_dir / "archive.zip"
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
        _process_archive_full,
        archive_path,
        tmp_dir,
        project_id,
        project.chunk_size,
        project.chunk_overlap,
        project.embedding_model,
        project.embedding_api_key,
        project.embedding_api_url,
        project.embedding_dimension,
        _OPERATIONS,
        operation_id,
    )

    return {"operation_id": operation_id, "message": "Обработка архива начата"}


async def _process_archive_full(
    archive_path,
    tmp_dir,
    project_id,
    chunk_size,
    chunk_overlap,
    embedding_model,
    embedding_api_key,
    embedding_api_url,
    embedding_dimension,
    operations,
    operation_id,
):
    """ZIP uses the same versioned index builder as single upload and chat."""
    from types import SimpleNamespace

    db = SessionLocal()
    skipped = []
    added_versions = {}
    try:
        project = lock_project(db, project_id)
        spec = indexing_settings(
            SimpleNamespace(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                embedding_api_url=embedding_api_url,
            )
        )
        added = []
        with zipfile.ZipFile(archive_path) as archive:
            seen = set()
            for member in archive.infolist():
                if member.is_dir():
                    continue
                name = member.filename.replace("\\", "/")
                if name in seen:
                    raise ValueError("В архиве повторяется имя документа.")
                seen.add(name)
                if get_parser_for(name) is None:
                    skipped.append(name)
                    continue
                content = archive.read(member)
                path = tmp_dir / (str(uuid.uuid4()) + Path(name).suffix)
                path.write_bytes(content)
                try:
                    process_file(path, name, chunk_size, chunk_overlap)
                except (EmptyPDFError, ParserError):
                    skipped.append(name)
                    continue
                added.append(register_document(db, project, name, content))
        if not added:
            raise ValueError("Архив не содержит документов для индексации.")
        index = plan_index(db, project, spec)
        added_versions = {source.id: source.document_version_id for source in added}
        db.commit()
        embedder = build_embedder(
            embedding_model, api_key=embedding_api_key, api_base=embedding_api_url
        )
        reused = not await ensure_index(db, index, build_vector_store(), embedder)
        mark_sources(db, index)
        db.commit()
        operations[operation_id] = {
            "status": "completed",
            "error": None,
            "result": {
                "chunks_count": sum(
                    index.document_chunks.get(str(version_id), 0)
                    for version_id in added_versions.values()
                ),
                "skipped_files": skipped,
                "index_reused": reused,
                **index_snapshot(index),
            },
        }
    except Exception as exc:
        db.rollback()
        for source_id, version_id in added_versions.items():
            source = db.get(DataSource, source_id)
            if source is not None and source.document_version_id == version_id:
                source.status = "failed"
                source.error_message = str(exc)
        db.commit()
        operations[operation_id] = {
            "status": "failed",
            "result": None,
            "error": str(exc),
        }
        logger.exception("Ошибка обработки архива %s", operation_id)
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get(
    "/status/{operation_id}",
    status_code=status.HTTP_200_OK,
    summary="Статус фоновой операции обработки архива",
)
async def get_operation_status(operation_id: str) -> Dict[str, Any]:
    """Возвращает статус фоновой операции.

    Args:
        operation_id: Идентификатор операции.

    Returns:
        Объект со статусом, результатом и ошибкой.

    Raises:
        HTTPException: 404, если операция не найдена.
    """
    op = _OPERATIONS.get(operation_id)
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Операция {operation_id} не найдена",
        )
    return {"operation_id": operation_id, **op}
