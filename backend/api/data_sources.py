"""
REST API для управления источниками данных проекта.

Эндпоинты:
    GET    /projects/{project_id}/data-sources   — список файлов проекта
    DELETE /data-sources/{data_source_id}        — удалить источник данных
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import DataSource
from backend.db.schemas import DataSourceListResponse, DataSourceResponse

router = APIRouter(prefix="/api/v1/public/provider", tags=["data-sources"])


@router.get(
    "/projects/{project_id}/data-sources",
    response_model=DataSourceListResponse,
    summary="Список источников данных проекта",
)
def list_data_sources(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> DataSourceListResponse:
    """Возвращает все источники данных (загруженные файлы) проекта.

    Args:
        project_id: UUID проекта.
        db: Сессия БД.

    Returns:
        Список DataSource с пагинацией.
    """
    items = (
        db.query(DataSource)
        .filter(DataSource.project_id == project_id)
        .order_by(DataSource.created_at.desc())
        .all()
    )
    return DataSourceListResponse(total=len(items), items=items)


@router.delete(
    "/data-sources/{data_source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить источник данных",
)
def delete_data_source(
    data_source_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """Удаляет источник данных по его UUID.

    Args:
        data_source_id: UUID источника данных.
        db: Сессия БД.

    Raises:
        HTTPException: 404, если источник не найден.
    """
    ds = db.query(DataSource).filter(DataSource.id == data_source_id).first()
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Источник данных не найден.",
        )
    db.delete(ds)
    db.commit()
