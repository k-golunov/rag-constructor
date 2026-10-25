"""Снимки действующих настроек и внутренний журнал статусов выполнения.

Полная цепочка ответа сохраняется отдельно в response_trace без публичных endpoints.
"""

from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID, uuid5
from urllib.parse import urlsplit, urlunsplit

from backend.config import settings
from backend.core.llm.openai_llm import OpenAILLM
from backend.db.models import ConfigurationVersion, ExecutionRun
from backend.core.response_trace import redact

COMPARISON_PARAMETERS = [
    "top_k",
    "max_context_chars",
    "score_threshold",
    "chunk_size",
    "chunk_overlap",
    "embedding_model",
    "llm_model",
    "system_prompt",
    "max_attempts",
]
UNSUPPORTED_PARAMETERS = {
    "split_by": "Только фиксированный рекурсивный алгоритм; sentences и tokens недоступны.",
    "chunking_strategy": "Только recursive; сравнение стратегий недоступно.",
    "extract_tables": "Настройка извлечения PDF-таблиц не реализована; только false.",
    "temperature": "Не задаётся приложением; значение по умолчанию провайдера.",
    "max_tokens": "Не задаётся приложением; значение по умолчанию провайдера.",
    "seed": "Не задаётся приложением; воспроизводимость генерации не гарантируется.",
}


def resolved_api_url(configured):
    return configured or settings.OPENAI_BASE_URL or "https://api.openai.com/v1"


def _public_url(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "invalid_url"
    return urlunsplit(
        (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", "")
    )


def execution_settings(project, mode, payload):
    limit = payload.max_context_chars
    result = {
        "mode": mode,
        "llm_provider": "openai_compatible",
        "llm_model": project.llm_model,
        "llm_api_url": _public_url(resolved_api_url(project.llm_api_url)),
        "system_prompt": project.system_prompt or OpenAILLM.DEFAULT_SYSTEM_PROMPT,
        "max_context_chars": limit if limit is not None else project.max_context_chars,
        "context_limit_unit": "characters",
        "context_limit_scope": "retrieved_text" if mode == "rag" else "provided_text",
        "generation_parameters": {"model": project.llm_model, "stream": False},
        "generation_parameter_names": ["model", "messages", "stream"],
        "unspecified_generation_parameters": "provider_defaults_unknown_to_application",
        "provider_defaults": ["temperature", "max_tokens", "seed"],
        "unsupported_parameters": dict(UNSUPPORTED_PARAMETERS),
        "comparison_parameters": (
            list(COMPARISON_PARAMETERS)
            if mode == "rag"
            else [
                "max_context_chars",
                "llm_model",
                "system_prompt",
                "max_attempts",
            ]
        ),
        "retry_policy": {
            "max_attempts": payload.max_attempts,
            "sdk_retries": 0,
            "backoff_ms": 100,
        },
        "max_attempts": payload.max_attempts,
    }
    if mode == "rag":
        result.update(
            {
                name: getattr(project, name)
                for name in (
                    "chunk_size",
                    "chunk_overlap",
                    "chunking_strategy",
                    "split_by",
                    "extract_tables",
                    "embedding_model",
                    "embedding_dimension",
                )
            }
        )
        result.update(
            top_k=payload.top_k if payload.top_k is not None else project.top_k,
            score_threshold=payload.score_threshold,
            vector_store="qdrant",
            collection_name=None,
            index_resolution="unresolved",
            embedding_api_url=_public_url(resolved_api_url(project.embedding_api_url)),
            embedding_provider=(
                "huggingface"
                if project.embedding_model.startswith("sentence-transformers/")
                else "openai_compatible"
            ),
        )
    return result


def start_run(db, project, mode, applied_settings, session_id=None, secrets=()):
    version = configuration_version(db, project, applied_settings, secrets)
    config_id, checksum = version.id, version.sha256
    serialized = json.dumps(redact(applied_settings, secrets), ensure_ascii=False)
    run = ExecutionRun(
        project_id=project.id,
        session_id=session_id,
        mode=mode,
        stage="retrieval" if mode == "rag" else "generation",
        applied_settings=json.loads(serialized),
        configuration_version_id=config_id,
        index_version_id=(
            UUID(applied_settings["index_version_id"])
            if applied_settings.get("index_version_id")
            else None
        ),
        details={"generation_attempted": False, "configuration_sha256": checksum},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def generation_details(request, context_chars):
    serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)
    return {
        "generation_attempted": True,
        "context_chars": context_chars,
        "message_count": len(request["messages"]),
        "request_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def fail_run(db, run, code):
    run.status = "error"
    run.error_code = code
    if code == "retrieval_error":
        run.details = {**run.details, "retrieval_status": "error"}
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


def complete_run(run):
    run.status = "success"
    run.stage = "completed"
    run.finished_at = datetime.now(timezone.utc)


def configuration_version(db, project, applied_settings, secrets=()):
    serialized = json.dumps(
        redact(
            {
                key: value
                for key, value in applied_settings.items()
                if key != "evaluation"
            },
            secrets,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    config_id = uuid5(project.id, checksum)
    # Serialize registration per project; copies never reference mutable project fields.
    from backend.db.models import Project

    db.query(Project).filter_by(id=project.id).with_for_update().one()
    version = db.get(ConfigurationVersion, config_id)
    if version is None:
        version = ConfigurationVersion(
            id=config_id,
            project_id=project.id,
            sha256=checksum,
            settings=json.loads(serialized),
        )
        db.add(version)
        db.flush()
    return version
