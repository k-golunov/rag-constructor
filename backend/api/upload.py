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

from backend.config import settings
from backend.core.factories import get_embedder, get_vector_store
from backend.core.ingestion.parser import (
    SUPPORTED_EXTENSIONS,
    EmptyPDFError,
    ParserError,
    get_parser_for,
)
from backend.core.ingestion.pipeline import process_archive, process_file
from backend.db.database import get_db
from backend.db.models import DataSource, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/provider/upload", tags=["upload"])

_OPERATIONS: Dict[str, Dict[str, Any]] = {}
_FORMATS_HINT = ", ".join(SUPPORTED_EXTENSIONS)


def _collection_name(project_id: UUID) -> str:
    return f"project_{str(project_id).replace('-', '_')}"


def _get_project(project_id: UUID, db: Session) -> Project:
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

    # Сохраняем файл в upload_dir
    upload_dir = Path(settings.UPLOAD_DIR) / str(project_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / filename
    content = await file.read()
    saved_path.write_bytes(content)

    # Парсинг и чанкинг
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp_path.write_bytes(content)

    try:
        chunks = process_file(tmp_path, filename, project.chunk_size, project.chunk_overlap)
    except EmptyPDFError as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except ParserError as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    # Запись DataSource в БД
    ds = DataSource(
        project_id=project_id,
        file_name=filename,
        file_path=str(saved_path),
        status="processing",
        chunks_count=0,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    # Эмбеддинг + индексация в Qdrant
    try:
        embedder = get_embedder(
            model_name=project.embedding_model,
            api_key=project.embedding_api_key,
            api_base=project.embedding_api_url,
        )
        texts = [c.text for c in chunks]
        embeddings = await embedder.embed(texts)
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb

        vector_store = get_vector_store()
        collection = _collection_name(project_id)
        await vector_store.create_collection(collection, project.embedding_dimension)
        await vector_store.upsert(collection, chunks)

        ds.status = "completed"
        ds.chunks_count = len(chunks)
    except Exception as exc:
        logger.error("Ошибка векторизации %s: %s", filename, exc)
        ds.status = "failed"
        ds.error_message = str(exc)
    finally:
        db.commit()

    return {
        "filename": filename,
        "project_id": str(project_id),
        "data_source_id": str(ds.id),
        "status": ds.status,
        "chunk_size": project.chunk_size,
        "chunk_overlap": project.chunk_overlap,
        "chunks_count": ds.chunks_count,
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


def _process_archive_full(
    archive_path: Path,
    tmp_dir: Path,
    project_id: UUID,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
    embedding_api_key: str | None,
    embedding_api_url: str | None,
    embedding_dimension: int,
    operations: Dict[str, Dict[str, Any]],
    operation_id: str,
) -> None:
    """Фоновая задача: парсинг, синхронный эмбеддинг и уплотнение в Qdrant.

    Args:
        archive_path: Путь к ZIP-архиву.
        tmp_dir: Временная директория (будет удалена после обработки).
        project_id: UUID проекта.
        chunk_size: Размер чанка.
        chunk_overlap: Перекрытие чанков.
        embedding_model: Имя модели эмбеддингов.
        embedding_api_key: API-ключ эмбеддера.
        embedding_api_url: URL API эмбеддера.
        embedding_dimension: Размерность векторов.
        operations: Словарь для хранения статуса операций.
        operation_id: Идентификатор операции.
    """
    import asyncio

    extract_dir = tmp_dir / "extracted"
    all_chunks = []
    skipped = []

    try:
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)

        for path in extract_dir.rglob("*"):
            if not path.is_file():
                continue
            if get_parser_for(path.name) is None:
                skipped.append(path.name)
                continue
            try:
                all_chunks.extend(
                    process_file(path, path.name, chunk_size, chunk_overlap)
                )
            except (EmptyPDFError, ParserError) as exc:
                logger.warning("Пропущен %s: %s", path.name, exc)
                skipped.append(path.name)

        # Синхронный эмбеддинг
        if all_chunks:
            embedder = get_embedder(
                model_name=embedding_model,
                api_key=embedding_api_key,
                api_base=embedding_api_url,
            )
            texts = [c.text for c in all_chunks]
            embeddings = embedder.embed_sync(texts)
            for chunk, emb in zip(all_chunks, embeddings):
                chunk.embedding = emb

            collection = _collection_name(project_id)
            vector_store = get_vector_store()
            asyncio.run(vector_store.create_collection(collection, embedding_dimension))
            asyncio.run(vector_store.upsert(collection, all_chunks))

        operations[operation_id] = {
            "status": "completed",
            "result": {
                "chunks_count": len(all_chunks),
                "skipped_files": skipped,
            },
            "error": None,
        }
    except Exception as exc:
        logger.exception("Фоновая обработка %s упала", operation_id)
        operations[operation_id] = {"status": "failed", "result": None, "error": str(exc)}
    finally:
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
