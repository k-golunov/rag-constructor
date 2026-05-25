"""
REST API для генерации ответов LLM с учётом RAG-контекста.

Эндпоинты:
    POST /projects/{project_id}/chat/generate — сгенерировать ответ по вопросу и контексту
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.factories import get_llm
from backend.db.database import get_db
from backend.db.models import Project
from backend.db.schemas import GenerateRequest, GenerateResponse

router = APIRouter(prefix="/api/v1/public/provider/projects", tags=["chat"])


def _get_project_or_404(project_id: UUID, db: Session) -> Project:
    """Возвращает проект по ID или бросает HTTP 404."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден.",
        )
    return project


@router.post(
    "/{project_id}/chat/generate",
    response_model=GenerateResponse,
    summary="Сгенерировать ответ LLM с учётом контекста",
)
async def generate_answer(
    project_id: UUID,
    payload: GenerateRequest,
    db: Session = Depends(get_db),
) -> GenerateResponse:
    """Принимает вопрос и найденные чанки, возвращает ответ LLM.

    Args:
        project_id: UUID проекта (определяет модель, ключ и системный промпт).
        payload: Вопрос, контекст и опциональная история диалога.
        db: Сессия БД.

    Returns:
        Объект с полем answer — текстовый ответ модели.

    Raises:
        HTTPException: 404 если проект не найден; 422 если LLM не сконфигурирован.
    """
    project = _get_project_or_404(project_id, db)

    if not project.llm_model or not project.llm_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="LLM не сконфигурирован: заполните llm_model и llm_api_key в настройках проекта.",
        )

    llm = get_llm(
        "openai",
        model=project.llm_model,
        api_key=project.llm_api_key,
        api_url=project.llm_api_url,
        system_prompt=project.system_prompt,
    )

    answer = await llm.generate(
        prompt=payload.query,
        context=payload.context,
        history=payload.history,
    )

    return GenerateResponse(answer=answer)
