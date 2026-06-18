import asyncio
import logging
from pathlib import Path

from .worker import celery_app
from backend.config import settings
from backend.core.factories import build_embedder, build_vector_store
from backend.core.ingestion.pipeline import process_file
from backend.db.database import SessionLocal

logger = logging.getLogger(__name__)


def _collection_name(project_id: str) -> str:
    return f"project_{project_id.replace('-', '_')}"


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
    try:
        path = Path(file_path)
        chunks = process_file(path, path.name, chunk_size, chunk_overlap)

        embedder = build_embedder(
            embedding_model,
            api_key=embedding_api_key,
            api_base=embedding_api_url,
        )
        texts = [c.text for c in chunks]
        embeddings = embedder.embed_sync(texts)
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb

        collection = _collection_name(project_id)
        vector_store = build_vector_store()
        asyncio.run(vector_store.create_collection(collection, embedding_dimension))
        asyncio.run(vector_store.upsert(collection, chunks))

        ds = db.query(DataSource).filter(DataSource.id == data_source_id).first()
        if ds:
            ds.status = "completed"
            ds.chunks_count = len(chunks)
            db.commit()

        return {"status": "success", "chunks_processed": len(chunks)}

    except Exception as exc:
        logger.exception("Ошибка обработки %s", file_path)
        from backend.db.models import DataSource

        ds = db.query(DataSource).filter(DataSource.id == data_source_id).first()
        if ds:
            ds.status = "failed"
            ds.error_message = str(exc)
            db.commit()
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
