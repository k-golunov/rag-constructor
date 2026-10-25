"""
REST API для RAG-чата.

Эндпоинты:
    POST /projects/{project_id}/chat              — задать вопрос ассистенту (полный RAG)
    GET  /projects/{project_id}/chat/{session_id} — история диалога
    POST /projects/{project_id}/chat/generate     — генерация ответа по готовому контексту
"""

import logging
from uuid import UUID
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.response_trace import ResponseTrace
from backend.core.rag import (
    execute_rag,
    _generate_traced as generate_traced,
    _execution_secrets,
)
from backend.db.rag_schemas import ChatRequest, ChatResponse
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
    resolved_api_url,
)
from backend.db.database import get_db
from backend.db.models import ChatHistory, Project
from backend.db.schemas import ChatHistoryResponse, GenerateRequest, GenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/provider", tags=["chat"])


def _get_project_or_404(project_id: UUID, db: Session) -> Project:
    """Возвращает проект по ID или бросает HTTP 404."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден.",
        )
    return project


async def _generate_traced(*args, **kwargs):
    return await generate_traced(*args, llm_factory=build_llm, **kwargs)


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
    return await execute_rag(
        project_id,
        payload,
        db,
        embedder_factory=build_embedder,
        store_factory=build_vector_store,
        generate=_generate_traced,
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
