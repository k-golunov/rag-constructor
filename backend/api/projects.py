"""
REST API для управления проектами RAG-конструктора.

Эндпоинты:
    POST   /projects/          — создать проект
    GET    /projects/          — список всех проектов
    GET    /projects/{id}      — получить проект по ID
    PATCH  /projects/{id}      — обновить настройки проекта
    DELETE /projects/{id}      — удалить проект
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Project
from backend.db.schemas import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/api/v1/public/provider/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новый проект",
)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> Project:
    """Создаёт новый RAG-проект с заданными настройками.

    Args:
        payload: Данные нового проекта (имя, модели, параметры чанкинга).
        db: Сессия БД (инжектируется через Depends).

    Returns:
        Созданный проект со всеми полями, включая id и created_at.
    """
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get(
    "/",
    response_model=ProjectListResponse,
    summary="Список всех проектов",
)
def list_projects(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    """Возвращает постранично список всех проектов.

    Args:
        skip: Сколько записей пропустить (offset).
        limit: Максимальное кол-во записей в ответе.
        db: Сессия БД.

    Returns:
        Объект с полями total и items.
    """
    total = db.query(Project).count()
    items = (
        db.query(Project)
        .order_by(Project.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return ProjectListResponse(total=total, items=items)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Получить проект по ID",
)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> Project:
    """Возвращает один проект по его UUID.

    Args:
        project_id: UUID проекта.
        db: Сессия БД.

    Returns:
        Объект проекта.

    Raises:
        HTTPException: 404, если проект не найден.
    """
    return _get_project_or_404(project_id, db)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Обновить настройки проекта",
)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> Project:
    """Частично обновляет поля проекта (только переданные поля).

    Args:
        project_id: UUID проекта.
        payload: Объект с обновляемыми полями (все опциональны).
        db: Сессия БД.

    Returns:
        Обновлённый объект проекта.

    Raises:
        HTTPException: 404, если проект не найден.
    """
    project = _get_project_or_404(project_id, db)

    # Применяем только те поля, которые явно переданы (exclude_unset=True)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить проект",
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """Удаляет проект и все связанные данные (каскадно).

    Args:
        project_id: UUID проекта.
        db: Сессия БД.

    Raises:
        HTTPException: 404, если проект не найден.
    """
    project = _get_project_or_404(project_id, db)
    db.delete(project)
    db.commit()
