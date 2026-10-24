import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from .worker import celery_app
from backend.core.factories import build_embedder, build_vector_store
from backend.db.database import SessionLocal
from backend.core.versioning import (
    register_document,
    plan_index,
    ensure_index,
    indexing_settings,
    lock_project,
)
from backend.core.versioning import mark_sources

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def process_document_task(
    self,
    project_id: str,
    data_source_id: str,
    file_path: str,
    chunk_size: int,
    chunk_overlap: int,
    embedding_model: str,
    embedding_dimension: int,
    embedding_api_key: str | None = None,
    embedding_api_url: str | None = None,
) -> dict:
    """Celery-задача полного RAG-пайплайна для одного файла.

    Шаги:
      1. Парсинг и чанкинг файла.
      2. Синхронный эмбеддинг чанков.
      3. Создание коллекции Qdrant (если не существует).
      4. Upsert чанков в Qdrant.
      5. Обновление статуса DataSource в БД.

    Args:
        project_id: UUID проекта (строка).
        data_source_id: UUID DataSource для обновления статуса.
        file_path: Абсолютный путь к файлу.
        chunk_size: Размер чанка.
        chunk_overlap: Перекрытие чанков.
        embedding_model: Имя модели эмбеддингов.
        embedding_dimension: Размерность векторов.
        embedding_api_key: API-ключ эмбеддера.
        embedding_api_url: URL API эмбеддера.

    Returns:
        Словарь со статусом и количеством чанков.
    """
    from backend.db.models import DataSource

    db = SessionLocal()
    target_version = None
    try:
        project = lock_project(db, UUID(project_id))
        ds = db.get(DataSource, UUID(data_source_id))
        if ds is None or not ds.is_active:
            return {"status": "superseded", "chunks_processed": 0}
        if ds.document_version_id is not None and Path(ds.file_path) != Path(file_path):
            return {"status": "superseded", "chunks_processed": 0}
        if ds.document_version_id is None:
            ds = register_document(
                db, project, ds.file_name, Path(file_path).read_bytes()
            )
        target_version = ds.document_version_id
        spec = indexing_settings(
            SimpleNamespace(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                embedding_api_url=embedding_api_url,
            )
        )
        index = plan_index(db, project, spec)
        db.commit()
        embedder = build_embedder(
            embedding_model, api_key=embedding_api_key, api_base=embedding_api_url
        )
        reused = not asyncio.run(
            ensure_index(db, index, build_vector_store(), embedder)
        )
        mark_sources(db, index)
        db.commit()
        return {
            "status": "success",
            "chunks_processed": index.document_chunks.get(str(target_version), 0),
            "index_version_id": str(index.id),
            "index_reused": reused,
        }

    except Exception as exc:
        logger.exception("Ошибка обработки %s", file_path)
        from backend.db.models import DataSource

        ds = db.get(DataSource, UUID(data_source_id))
        if ds and ds.document_version_id == target_version:
            ds.status = "failed"
            ds.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
