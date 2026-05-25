"""
REST API для RAG-чата.

Эндпоинты:
    POST /projects/{project_id}/chat              — задать вопрос ассистенту
    GET  /projects/{project_id}/chat/{session_id} — история диалога
"""

import logging
import uuid as uuid_module
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.factories import get_embedder, get_llm, get_vector_store
from backend.db.database import get_db
from backend.db.models import ChatHistory, Project
from backend.db.schemas import ChatHistoryResponse

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
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден.")

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
        embedder = get_embedder(
            model_name=project.embedding_model,
            api_key=project.embedding_api_key,
            api_base=project.embedding_api_url,
        )
        [query_vector] = await embedder.embed([payload.question])

        vector_store = get_vector_store()
        docs = await vector_store.search(_collection_name(project_id), query_vector, limit=5)
        context = "\n\n".join(doc.text for doc in docs)
        sources = list({doc.metadata.get("source", "") for doc in docs if doc.metadata.get("source")})
    except Exception as exc:
        logger.warning("Не удалось получить контекст из Qdrant: %s", exc)

    # Вызов LLM
    try:
        llm = get_llm(
            model_name=project.llm_model,
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
