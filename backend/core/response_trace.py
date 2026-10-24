"""Persist the response chain and each attempt without credentials."""

import asyncio
from datetime import datetime, timezone
from time import perf_counter
import re

import httpx
from openai import APIConnectionError, APITimeoutError


def token_usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, name, None)
        result[name] = value if type(value) is int and value >= 0 else None
    for name, fields in (
        ("prompt_tokens_details", ("cached_tokens", "audio_tokens")),
        (
            "completion_tokens_details",
            (
                "reasoning_tokens",
                "audio_tokens",
                "accepted_prediction_tokens",
                "rejected_prediction_tokens",
            ),
        ),
    ):
        details = getattr(usage, name, None)
        counts = {field: getattr(details, field, None) for field in fields}
        counts = {
            field: count if type(count) is int and count >= 0 else None
            for field, count in counts.items()
        }
        if any(count is not None for count in counts.values()):
            result[name] = counts
    return result if any(value is not None for value in result.values()) else None


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {
            redact(key, secrets): (
                "[REDACTED]"
                if key.lower()
                in {
                    "api_key",
                    "embedding_api_key",
                    "llm_api_key",
                    "authorization",
                    "password",
                    "access_token",
                }
                else redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(
            {item for item in secrets if isinstance(item, str) and item},
            key=len,
            reverse=True,
        ):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
        value = re.sub(r"(?i)(bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        return re.sub(
            r"(?i)([\"']?(?:api[_-]?key|access_token|token|password|key|authorization)[\"']?\s*[:=]\s*[\"']?)[^&\s\"'<>]+",
            r"\1[REDACTED]",
            value,
        )
    return value


def is_retryable(exc):
    status = getattr(exc, "status_code", None)
    return isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            httpx.NetworkError,
            httpx.TimeoutException,
            APIConnectionError,
            APITimeoutError,
        ),
    ) or (type(status) is int and (status == 429 or 500 <= status < 600))


class ResponseTrace:
    def __init__(
        self, db, run, question, history, system_prompt, secrets=(), started=None
    ):
        self.db, self.run, self.secrets = db, run, secrets
        self.started = started if started is not None else perf_counter()
        self.last_error = None
        self.data = {
            "schema_version": 1,
            "question": question,
            "system_prompt": system_prompt,
            "history": history,
            "retrieved_chunks": None,
            "context": None,
            "messages": None,
            "answer": None,
            "retrieval_status": "not_started",
            "timings_ms": {},
            "attempts": [],
            "errors": [],
            "token_usage": {
                "index_preparation": None,
                "query_embedding": None,
                "generation": None,
            },
            "token_usage_scope": "last_attempt_per_stage",
            "redaction_applied": False,
        }
        self.save()

    def clean(self, value):
        return redact(value, self.secrets)

    def save(self):
        self.data["timings_ms"]["total"] = (perf_counter() - self.started) * 1000
        self.data["retry_count"] = sum(
            entry["number"] > 1 for entry in self.data["attempts"]
        )
        cleaned = self.clean(self.data)
        cleaned["redaction_applied"] = cleaned != self.data
        self.run.response_trace = cleaned
        self.db.commit()

    def record_error(self, stage, exc, attempt=None):
        self.last_error = exc
        error = {
            "stage": stage,
            "attempt": attempt,
            "type": type(exc).__name__,
            "message": self.clean(str(exc))[:2000],
            "retryable": is_retryable(exc),
        }
        status = getattr(exc, "status_code", None)
        error["http_status"] = status if type(status) is int else None
        self.data["errors"].append(error)
        return error

    async def stage(self, name, operation, max_attempts=1, usage_getter=None):
        stage_started = perf_counter()
        for number in range(1, max_attempts + 1):
            entry = {
                "stage": name,
                "number": number,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": None,
                "token_usage": None,
            }
            self.data["attempts"].append(entry)
            self.save()
            attempt_started = perf_counter()
            try:
                value = await operation()
            except Exception as exc:
                entry.update(
                    status="error",
                    duration_ms=(perf_counter() - attempt_started) * 1000,
                )
                entry["token_usage"] = usage_getter() if usage_getter else None
                if usage_getter:
                    self.data["token_usage"][name] = entry["token_usage"]
                entry["error"] = self.record_error(name, exc, number)
                retry = is_retryable(exc) and number < max_attempts
                entry["will_retry"] = retry
                self.data["timings_ms"][name] = (perf_counter() - stage_started) * 1000
                self.save()
                if not retry:
                    raise
                await asyncio.sleep(0.1 * 2 ** (number - 1))
            else:
                usage = usage_getter() if usage_getter else None
                entry.update(
                    status="success",
                    duration_ms=(perf_counter() - attempt_started) * 1000,
                    token_usage=usage,
                )
                self.data["timings_ms"][name] = (perf_counter() - stage_started) * 1000
                if usage_getter:
                    self.data["token_usage"][name] = usage
                self.save()
                return value

    def search_result(self, docs):
        self.data["retrieval_status"] = (
            "success" if any(doc.text.strip() for doc in docs) else "no_context"
        )
        self.data["retrieved_chunks"] = [
            {
                "rank": rank,
                "id": doc.id,
                "text": doc.text,
                "score": doc.score,
                "source": doc.metadata.get("source"),
                "document_version_id": doc.metadata.get("document_version_id"),
                "document_sha256": doc.metadata.get("document_sha256"),
                "metadata": doc.metadata,
            }
            for rank, doc in enumerate(docs, 1)
        ]
        self.save()

    def context(self, context, chunk_ids, duration_ms=None):
        self.data.update(context=context, context_chunk_ids=chunk_ids)
        self.data["timings_ms"]["context_build"] = duration_ms
        self.save()

    def request(self, request):
        self.data["messages"] = request["messages"]
        self.save()

    def failure(self, stage, exc):
        if self.last_error is not exc:
            self.record_error(stage, exc)
        if stage == "retrieval":
            self.data["retrieval_status"] = "error"
        self.save()

    def answer(self, answer):
        self.data["answer"] = answer
        self.save()
