"""
REST API для поиска релевантных чанков в векторном хранилище.

Эндпоинты:
    POST /api/v1/public/provider/projects/{project_id}/search — найти релевантные чанки
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.config import settings
from backend.core.factories import get_vector_store
from backend.db.database import get_db
from backend.db.models import Project
from backend.db.schemas import DocumentResponse, SearchRequest, SearchResponse

router = APIRouter(prefix="/api/v1/public/provider/projects", tags=["search"])


def _get_project_or_404(project_id: UUID, db: Session) -> Project:
    """Возвращает проект по ID или бросает HTTP 404.

    Args:
        project_id: Идентификатор проекта.
        db: Сессия SQLAlchemy.

    Returns:
        Объект Project.

    Raises:
        HTTPException: 404, если проект не найден.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден.",
        )
    return project


@router.post(
    "/{project_id}/search",
    response_model=SearchResponse,
    summary="Найти релевантные чанки по вектору запроса",
)
async def search_chunks(
    project_id: UUID,
    payload: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Ищет релевантные чанки в векторном хранилище проекта.

    Коллекция в Qdrant соответствует ``str(project_id)`` проекта.

    Args:
        project_id: UUID проекта.
        payload: Вектор запроса и лимит результатов.
        db: Сессия БД.

    Returns:
        Список наиболее релевантных документов с метаданными.

    Raises:
        HTTPException: 404, если проект не найден.
    """
    _get_project_or_404(project_id, db)

    store = get_vector_store(
        "qdrant",
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
    )
    documents = await store.search(
        collection_name=str(project_id),
        query_vector=payload.query_vector,
        limit=payload.limit,
    )

    items = [
        DocumentResponse(id=doc.id, text=doc.text, metadata=doc.metadata)
        for doc in documents
    ]
    return SearchResponse(items=items, total=len(items))
