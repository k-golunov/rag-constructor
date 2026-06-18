"""
REST API для RAG-чата.

Эндпоинты:
    POST /projects/{project_id}/chat              — задать вопрос ассистенту (полный RAG)
    GET  /projects/{project_id}/chat/{session_id} — история диалога
    POST /projects/{project_id}/chat/generate     — генерация ответа по готовому контексту
"""

import logging
import uuid as uuid_module
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.factories import build_embedder, build_llm, build_vector_store, get_llm
from backend.db.database import get_db
from backend.db.models import ChatHistory, Project
from backend.db.schemas import ChatHistoryResponse, GenerateRequest, GenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/provider", tags=["chat"])


class ChatRequest(BaseModel):
    """Запрос к RAG-ассистенту."""

    question: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Ответ RAG-ассистента."""

    answer: str
    session_id: str
    sources: List[str]


def _collection_name(project_id: UUID) -> str:
    return f"project_{str(project_id).replace('-', '_')}"


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
    "/projects/{project_id}/chat",
    response_model=ChatResponse,
    summary="Задать вопрос RAG-ассистенту",
)
async def chat(
    project_id: UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Обрабатывает вопрос пользователя через RAG-пайплайн.

    Шаги:
      1. Эмбеддинг вопроса.
      2. Поиск релевантных чанков в Qdrant.
      3. Передача контекста + вопроса в LLM.
      4. Сохранение сообщений в БД.

    Args:
        project_id: UUID проекта.
        payload: Вопрос и опциональный session_id.
        db: Сессия БД.

    Returns:
        Ответ ассистента, session_id и список источников.

    Raises:
        HTTPException: 404 если проект не найден, 502 при ошибке LLM.
    """
    project = _get_project_or_404(project_id, db)

    session_id = payload.session_id or str(uuid_module.uuid4())
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный session_id.")

    # История диалога для LLM
    history_records = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.project_id == project_id,
            ChatHistory.session_id == session_uuid,
        )
        .order_by(ChatHistory.created_at)
        .all()
    )
    history = [{"role": r.role, "content": r.content} for r in history_records]

    # Поиск контекста в Qdrant
    context = ""
    sources: List[str] = []
    try:
        embedder = build_embedder(
            project.embedding_model,
            api_key=project.embedding_api_key,
            api_base=project.embedding_api_url,
        )
        [query_vector] = await embedder.embed([payload.question])

        vector_store = build_vector_store()
        docs = await vector_store.search(_collection_name(project_id), query_vector, limit=5)
        context = "\n\n".join(doc.text for doc in docs)
        sources = list({doc.metadata.get("source", "") for doc in docs if doc.metadata.get("source")})
    except Exception as exc:
        logger.warning("Не удалось получить контекст из Qdrant: %s", exc)

    # Вызов LLM
    try:
        llm = build_llm(
            project.llm_model,
            system_prompt=project.system_prompt,
            api_key=project.llm_api_key,
            api_base=project.llm_api_url,
        )
        answer = await llm.generate(prompt=payload.question, context=context, history=history)
    except Exception as exc:
        logger.error("Ошибка LLM: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ошибка LLM: {exc}")

    # Сохранение в БД
    db.add(ChatHistory(
        project_id=project_id,
        session_id=session_uuid,
        role="user",
        content=payload.question,
    ))
    db.add(ChatHistory(
        project_id=project_id,
        session_id=session_uuid,
        role="assistant",
        content=answer,
    ))
    db.commit()

    return ChatResponse(answer=answer, session_id=session_id, sources=sources)


@router.post(
    "/projects/{project_id}/chat/generate",
    response_model=GenerateResponse,
    summary="Сгенерировать ответ LLM по готовому контексту",
)
async def generate_answer(
    project_id: UUID,
    payload: GenerateRequest,
    db: Session = Depends(get_db),
) -> GenerateResponse:
    """Принимает вопрос и готовый контекст, возвращает ответ LLM (без ретрива).

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


@router.get(
    "/projects/{project_id}/chat/{session_id}",
    response_model=ChatHistoryResponse,
    summary="История диалога",
)
def get_chat_history(
    project_id: UUID,
    session_id: UUID,
    db: Session = Depends(get_db),
) -> ChatHistoryResponse:
    """Возвращает историю диалога по session_id.

    Args:
        project_id: UUID проекта.
        session_id: UUID сессии.
        db: Сессия БД.

    Returns:
        Объект ChatHistoryResponse со списком сообщений.
    """
    messages = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.project_id == project_id,
            ChatHistory.session_id == session_id,
        )
        .order_by(ChatHistory.created_at)
        .all()
    )
    return ChatHistoryResponse(
        session_id=session_id,
        project_id=project_id,
        messages=messages,
    )
