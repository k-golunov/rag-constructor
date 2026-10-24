"""
REST API для RAG-чата.

Эндпоинты:
    POST /projects/{project_id}/chat              — задать вопрос ассистенту (полный RAG)
    GET  /projects/{project_id}/chat/{session_id} — история диалога
    POST /projects/{project_id}/chat/generate     — генерация ответа по готовому контексту
"""

import logging
import os
import uuid as uuid_module
from typing import List, Literal, Optional
from uuid import UUID
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.projects import validate_project_configuration
from backend.config import settings
from backend.core.response_trace import ResponseTrace
from backend.core.question_sets import evaluation_example
from backend.core.factories import (
    build_embedder,
    build_llm,
    build_vector_store,
)
from backend.core.execution import (
    execution_settings,
    start_run,
    fail_run,
    complete_run,
    generation_details,
    resolved_api_url,
)
from backend.core.versioning import plan_index, ensure_index, index_snapshot
from backend.db.database import get_db
from backend.db.models import ChatHistory, Project
from backend.db.schemas import ChatHistoryResponse, GenerateRequest, GenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/provider", tags=["chat"])


class ChatRequest(BaseModel):
    """Запрос к RAG-ассистенту."""

    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1)
    session_id: Optional[str] = None
    score_threshold: Optional[float] = Field(default=None, ge=-1, le=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=100)
    max_context_chars: Optional[int] = Field(default=None, ge=1, le=100000)
    max_attempts: int = Field(1, ge=1, le=3)
    question_set_version_id: Optional[UUID] = None
    question_example_id: Optional[UUID] = None


class ChatResponse(BaseModel):
    """Ответ RAG-ассистента."""

    answer: str
    session_id: str
    sources: List[str]
    retrieval_status: Literal["success", "no_context"]
    applied_settings: dict
    context_chars: int
    run_id: UUID
    configuration_version_id: UUID


def _get_project_or_404(project_id: UUID, db: Session) -> Project:
    """Возвращает проект по ID или бросает HTTP 404."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден.",
        )
    return project


def _execution_secrets(project):
    return (
        project.embedding_api_key,
        project.llm_api_key,
        settings.OPENAI_API_KEY,
        settings.QDRANT_API_KEY,
        os.getenv("OPENAI_API_KEY"),
    )


async def _generate_traced(
    db, run, trace, applied, key, url, question, context, history, max_attempts
):
    usage = {"value": None}

    async def operation():
        usage["value"] = None
        llm = build_llm(
            applied["llm_model"],
            system_prompt=applied["system_prompt"],
            api_key=key,
            api_base=url,
            max_context_chars=applied["max_context_chars"],
        )
        request = llm.generation_request(question, context, history)
        trace.request(request)
        run.details = {**run.details, **generation_details(request, len(context))}
        db.commit()
        try:
            return await llm.generate(prompt=question, context=context, history=history)
        finally:
            usage["value"] = getattr(llm, "last_usage", None)

    return await trace.stage(
        "generation", operation, max_attempts, lambda: usage["value"]
    )


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
        HTTPException: 404 если проект не найден, 502 при ошибке retrieval или LLM.
    """
    execution_started = perf_counter()
    project = _get_project_or_404(project_id, db)
    validate_project_configuration(project)
    evaluation = None
    if (
        payload.question_set_version_id is not None
        or payload.question_example_id is not None
    ):
        if payload.session_id is not None:
            raise HTTPException(
                422, "Вопрос из набора выполняется отдельно, без истории сессии."
            )
        evaluation = evaluation_example(
            db,
            project_id,
            payload.question_set_version_id,
            payload.question_example_id,
            payload.question,
        )
    applied_settings = execution_settings(project, "rag", payload)
    if evaluation is not None:
        applied_settings["evaluation"] = {
            key: value for key, value in evaluation.items() if key != "example"
        }
        applied_settings["evaluation"].update(
            example_id=evaluation["example"]["id"], split=evaluation["example"]["split"]
        )
    provider_options = {
        "embedding_key": project.embedding_api_key,
        "embedding_url": resolved_api_url(project.embedding_api_url),
        "llm_key": project.llm_api_key,
        "llm_url": resolved_api_url(project.llm_api_url),
    }

    session_id = payload.session_id or str(uuid_module.uuid4())
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный session_id."
        )

    # История диалога для LLM
    history_records = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.project_id == project_id,
            ChatHistory.session_id == session_uuid,
        )
        .order_by(ChatHistory.created_at, ChatHistory.id)
        .all()
    )
    history = [{"role": r.role, "content": r.content} for r in history_records]
    index_error = None
    resolution_started = perf_counter()
    try:
        index = plan_index(db, project)
        applied_settings.update(index_snapshot(index))
    except Exception as exc:
        db.rollback()
        index_error = exc
    resolution_duration = (perf_counter() - resolution_started) * 1000
    if (
        evaluation is not None
        and index_error is None
        and index.documents_sha256 != evaluation["documents_sha256"]
    ):
        db.rollback()
        raise HTTPException(
            409,
            "Состав документов индекса не совпадает с версией набора вопросов. Выберите исходный состав либо создайте новую версию набора.",
        )
    secrets = _execution_secrets(project) + (
        provider_options["llm_key"],
        provider_options["embedding_key"],
    )
    run = start_run(db, project, "rag", applied_settings, session_uuid, secrets)
    trace = ResponseTrace(
        db,
        run,
        payload.question,
        history,
        applied_settings["system_prompt"],
        secrets,
        execution_started,
    )
    if evaluation is not None:
        run.question_set_version_id = payload.question_set_version_id
        run.question_example_id = payload.question_example_id
        run.details = {
            **run.details,
            "question_example_snapshot": trace.clean(evaluation),
        }
        trace.save()
    trace.data["timings_ms"]["index_resolution"] = resolution_duration

    # Поиск контекста в Qdrant
    context = ""
    sources: List[str] = []
    try:
        if index_error is not None:
            raise index_error
        embedder = build_embedder(
            applied_settings["embedding_model"],
            api_key=provider_options["embedding_key"],
            api_base=provider_options["embedding_url"],
        )
        vector_store = build_vector_store()
        index_usage = {"value": None}

        async def prepare_index():
            built = None
            index_usage["value"] = None
            try:
                built = await ensure_index(db, index, vector_store, embedder)
                return built
            finally:
                index_usage["value"] = (
                    getattr(embedder, "last_usage", None)
                    if built is not False
                    else None
                )

        built = await trace.stage(
            "index_preparation",
            prepare_index,
            payload.max_attempts,
            lambda: index_usage["value"],
        )
        run.details = {**run.details, "index_reused": not built}

        async def embed_question():
            [query_vector] = await embedder.embed([payload.question])
            if len(query_vector) != applied_settings["embedding_dimension"]:
                raise ValueError(
                    "Размерность эмбеддинга вопроса не соответствует настройкам индекса."
                )
            return query_vector

        query_vector = await trace.stage(
            "query_embedding",
            embed_question,
            payload.max_attempts,
            lambda: getattr(embedder, "last_usage", None),
        )
        docs = await trace.stage(
            "search",
            lambda: vector_store.search(
                index.collection_name,
                query_vector,
                limit=applied_settings["top_k"],
                score_threshold=payload.score_threshold,
            ),
            payload.max_attempts,
        )
        trace.search_result(docs)
        context_started = perf_counter()
        docs = [doc for doc in docs if doc.text.strip()]
        retrieval_status = "success" if docs else "no_context"
        context_parts = []
        context_docs = []
        remaining = applied_settings["max_context_chars"]
        for doc in docs:
            separator_size = 2 if context_parts else 0
            if remaining <= separator_size:
                break
            part = doc.text[: remaining - separator_size]
            context_parts.append(part)
            context_docs.append(doc)
            remaining -= len(part) + separator_size
        context = "\n\n".join(context_parts)
        sources = list(
            dict.fromkeys(
                doc.metadata.get("source", "")
                for doc in context_docs
                if doc.metadata.get("source")
            )
        )
        trace.context(
            context,
            [doc.id for doc in context_docs],
            (perf_counter() - context_started) * 1000,
        )
    except Exception as exc:
        logger.warning("Ошибка retrieval: %s", type(exc).__name__)
        trace.failure("retrieval", exc)
        fail_run(db, run, "retrieval_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "status": "error",
                "retrieval_status": "error",
                "code": "retrieval_error",
                "run_id": str(run.id),
                "applied_settings": run.applied_settings,
                "message": "Не удалось выполнить поиск по документам. Проверьте Qdrant и настройки эмбеддингов.",
            },
        ) from exc

    # Вызов LLM
    run.stage = "generation"
    run.details = {
        **run.details,
        "retrieval_status": retrieval_status,
        "retrieved_count": len(docs),
        "context_chunks_count": len(context_docs),
        "context_chars": len(context),
        "retrieved_index_settings": [
            doc.metadata.get("index_settings") for doc in docs
        ],
    }
    db.commit()
    try:
        answer = await _generate_traced(
            db,
            run,
            trace,
            applied_settings,
            provider_options["llm_key"],
            provider_options["llm_url"],
            payload.question,
            context,
            history,
            payload.max_attempts,
        )
        trace.answer(answer)
    except Exception as exc:
        logger.warning("Ошибка LLM: %s", type(exc).__name__)
        trace.failure("generation", exc)
        fail_run(db, run, "generation_error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "status": "error",
                "code": "generation_error",
                "run_id": str(run.id),
                "configuration_version_id": str(run.configuration_version_id),
                "applied_settings": run.applied_settings,
                "message": "Не удалось выполнить генерацию ответа.",
            },
        ) from exc

    # Сохранение в БД
    if evaluation is None:
        _save_chat_messages(
            db,
            project_id,
            session_uuid,
            payload.question,
            answer,
            trace,
            run,
            retrieval_status,
            context,
            sources,
            docs,
            context_docs,
        )
    complete_run(run)
    trace.save()

    return ChatResponse(
        answer=answer,
        session_id=session_id,
        sources=sources,
        retrieval_status=retrieval_status,
        applied_settings=run.applied_settings,
        context_chars=len(context),
        run_id=run.id,
        configuration_version_id=run.configuration_version_id,
    )


def _save_chat_messages(
    db,
    project_id,
    session_uuid,
    question,
    answer,
    trace,
    run,
    retrieval_status,
    context,
    sources,
    docs,
    context_docs,
):
    db.add(
        ChatHistory(
            project_id=project_id,
            session_id=session_uuid,
            role="user",
            content=trace.clean(question),
        )
    )
    db.add(
        ChatHistory(
            project_id=project_id,
            session_id=session_uuid,
            role="assistant",
            content=trace.clean(answer),
            rag_metadata=trace.clean(
                {
                    "run_id": str(run.id),
                    "retrieval_status": retrieval_status,
                    "applied_settings": run.applied_settings,
                    "context": context,
                    "sources": sources,
                    "retrieved_chunks": [
                        {"id": doc.id, "metadata": doc.metadata} for doc in docs
                    ],
                    "context_chunk_ids": [doc.id for doc in context_docs],
                }
            ),
        )
    )


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
    execution_started = perf_counter()
    project = _get_project_or_404(project_id, db)
    evaluation = None
    if (
        payload.question_set_version_id is not None
        or payload.question_example_id is not None
    ):
        evaluation = evaluation_example(
            db,
            project_id,
            payload.question_set_version_id,
            payload.question_example_id,
            payload.query,
        )

    if not project.llm_model or not project.llm_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="LLM не сконфигурирован: заполните llm_model и llm_api_key в настройках проекта.",
        )

    applied_settings = execution_settings(project, "generate", payload)
    if evaluation is not None:
        applied_settings["evaluation"] = {
            key: value for key, value in evaluation.items() if key != "example"
        }
        applied_settings["evaluation"].update(
            example_id=evaluation["example"]["id"], split=evaluation["example"]["split"]
        )
    llm_key = project.llm_api_key
    llm_url = resolved_api_url(project.llm_api_url)
    secrets = _execution_secrets(project)
    run = start_run(
        db, project, "generate", applied_settings, payload.session_id, secrets
    )
    trace = ResponseTrace(
        db,
        run,
        payload.query,
        payload.history or [],
        applied_settings["system_prompt"],
        secrets,
        execution_started,
    )
    trace.data["retrieval_status"] = "not_applicable"
    if evaluation is not None:
        run.question_set_version_id = payload.question_set_version_id
        run.question_example_id = payload.question_example_id
        run.details = {
            **run.details,
            "question_example_snapshot": trace.clean(evaluation),
        }
    context_started = perf_counter()
    context = payload.context[: applied_settings["max_context_chars"]]
    trace.context(context, [], (perf_counter() - context_started) * 1000)
    try:
        answer = await _generate_traced(
            db,
            run,
            trace,
            applied_settings,
            llm_key,
            llm_url,
            payload.query,
            context,
            payload.history,
            payload.max_attempts,
        )
        trace.answer(answer)
    except Exception as exc:
        logger.warning("Ошибка генерации по готовому контексту: %s", type(exc).__name__)
        trace.failure("generation", exc)
        fail_run(db, run, "generation_error")
        raise HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "code": "generation_error",
                "run_id": str(run.id),
                "applied_settings": run.applied_settings,
                "message": "Не удалось выполнить генерацию ответа.",
            },
        ) from exc
    complete_run(run)
    trace.save()
    return GenerateResponse(
        answer=answer,
        run_id=run.id,
        applied_settings=run.applied_settings,
        context_chars=len(context),
        configuration_version_id=run.configuration_version_id,
    )


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
        .order_by(ChatHistory.created_at, ChatHistory.id)
        .all()
    )
    return ChatHistoryResponse(
        session_id=session_id,
        project_id=project_id,
        messages=messages,
    )
