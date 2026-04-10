from .worker import celery_app
from typing import List
import asyncio

@celery_app.task(bind=True, max_retries=3)
def process_document_task(self, project_id: str, file_path: str, chunk_size: int, chunk_overlap: int):
    """
    Задача, которая:
    1. Парсит файл (использует Parser из ingestion)
    2. Разбивает на чанки (Splitter)
    3. Получает эмбеддинги (Embedder)
    4. Сохраняет в векторную БД (VectorStore)
    """
    # TODO: здесь будет вызов модулей через фабрики
    print(f"Processing {file_path} for project {project_id}...")
    return {"status": "success", "chunks_processed": 0}