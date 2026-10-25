"""One RAG executor for interactive chat and isolated batch questions."""

import logging
import os
import uuid as uuid_module
from time import perf_counter
from uuid import UUID
from typing import List
from fastapi import HTTPException, status
from backend.api.projects import validate_project_configuration
from backend.config import settings
from backend.core.question_sets import evaluation_example, project_or_404
from backend.core.factories import build_llm
from backend.core.response_trace import ResponseTrace
from backend.core.execution import (
    execution_settings,
    start_run,
    fail_run,
    complete_run,
    generation_details,
    resolved_api_url,
)
from backend.core.versioning import plan_index, ensure_index, index_snapshot
from backend.db.models import ChatHistory
from backend.db.rag_schemas import ChatResponse

logger = logging.getLogger(__name__)


def _execution_secrets(project):
    return (
        project.embedding_api_key,
        project.llm_api_key,
        settings.OPENAI_API_KEY,
        settings.QDRANT_API_KEY,
        os.getenv("OPENAI_API_KEY"),
    )


async def _generate_traced(
    db,
    run,
    trace,
    applied,
    key,
    url,
    question,
    context,
    history,
    max_attempts,
    llm_factory=build_llm,
):
    usage = {"value": None}

    async def operation():
        usage["value"] = None
        llm = llm_factory(
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


async def execute_rag(
    project_id,
    payload,
    db,
    *,
    embedder_factory,
    store_factory,
    generate,
    project_override=None,
    prepared=None,
    on_started=None
):
    execution_started = perf_counter()
    project = project_override or project_or_404(db, project_id)
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
    applied_settings = (
        dict(prepared["settings"])
        if prepared
        else execution_settings(project, "rag", payload)
    )
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
        index = prepared["index"] if prepared else plan_index(db, project)
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
    if on_started is not None:
        on_started(run)
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
        embedder = embedder_factory(
            applied_settings["embedding_model"],
            api_key=provider_options["embedding_key"],
            api_base=provider_options["embedding_url"],
        )
        vector_store = store_factory()
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
        answer = await generate(
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
