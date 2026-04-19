import logging
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List
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

from backend.core.Document import Document
from backend.core.ingestion.parser import EmptyPDFError, ParserError, get_parser_for
from backend.core.ingestion.splitter import RecursiveCharacterTextSplitter
from backend.db.database import get_db
from backend.db.models import Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])

_OPERATIONS: Dict[str, Dict[str, Any]] = {}


def _get_project_or_404(project_id: UUID, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден",
        )
    return project


def _process_single_file(
    file_path: Path,
    source_name: str,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Document]:
    parser = get_parser_for(source_name)
    if parser is None:
        raise ParserError(f"Неподдерживаемое расширение: {source_name}")
    text = parser.parse(str(file_path))
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return splitter.split_text(text, metadata={"source": source_name})


@router.post("/single", summary="Загрузить один файл и получить чанки")
async def upload_single(
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    project = _get_project_or_404(project_id, db)

    filename = file.filename or "unknown"
    if get_parser_for(filename) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемое расширение файла: {filename}",
        )

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(await file.read())

    try:
        chunks = _process_single_file(
            tmp_path, filename, project.chunk_size, project.chunk_overlap
        )
    except EmptyPDFError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
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
    summary="Загрузить ZIP-архив (фоновая обработка)",
)
async def upload_archive(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    project = _get_project_or_404(project_id, db)

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
        _process_archive,
        operation_id,
        archive_path,
        tmp_dir,
        project.chunk_size,
        project.chunk_overlap,
    )

    return {"operation_id": operation_id, "message": "Обработка архива начата"}


def _process_archive(
    operation_id: str,
    archive_path: Path,
    tmp_dir: Path,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    extract_dir = tmp_dir / "extracted"
    all_chunks: List[Document] = []
    skipped: List[str] = []
    try:
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)

        for path in extract_dir.rglob("*"):
            if not path.is_file():
                continue
            if get_parser_for(path.name) is None:
                logger.warning("Пропущен неподдерживаемый файл: %s", path.name)
                skipped.append(path.name)
                continue
            try:
                all_chunks.extend(
                    _process_single_file(path, path.name, chunk_size, chunk_overlap)
                )
            except EmptyPDFError as exc:
                logger.warning("Пропущен пустой PDF %s: %s", path.name, exc)
                skipped.append(path.name)
            except ParserError as exc:
                logger.warning("Ошибка парсинга %s: %s", path.name, exc)
                skipped.append(path.name)

        _OPERATIONS[operation_id] = {
            "status": "completed",
            "result": {
                "chunks_count": len(all_chunks),
                "skipped_files": skipped,
                "chunks": [c.model_dump() for c in all_chunks],
            },
            "error": None,
        }
    except Exception as exc:
        logger.exception("Фоновая обработка %s упала", operation_id)
        _OPERATIONS[operation_id] = {"status": "failed", "result": None, "error": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/status/{operation_id}", summary="Статус фоновой операции обработки архива")
async def get_operation_status(operation_id: str) -> Dict[str, Any]:
    op = _OPERATIONS.get(operation_id)
    if op is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Операция {operation_id} не найдена",
        )
    return {"operation_id": operation_id, **op}
