"""Audit the settings of each accepted attempt without extra text copies."""

import hashlib
import json
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.core.llm.openai_llm import OpenAILLM
from backend.db.models import ExecutionRun
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
upload = pipeline.upload
QUESTION = pipeline.QUESTION


def get_run(run_id):
    with TestingSessionLocal() as db:
        return db.get(ExecutionRun, UUID(run_id))


@pytest.mark.parametrize("mode", ["rag", "generate"])
def test_successful_attempt_has_complete_settings_and_exact_request_fingerprint(
    rag, mode
):
    if mode == "rag":
        assert upload(rag).json()["status"] == "completed"
        url = rag.chat_url
        payload = {"question": QUESTION, "top_k": 2, "max_context_chars": 35}
    else:
        url = f"{rag.chat_url}/generate"
        payload = {"query": "test", "context": "x" * 80, "max_context_chars": 35}
    response = rag.client.post(url, json=payload)
    assert response.status_code == 200
    result = response.json()
    run = get_run(result["run_id"])
    assert run.mode == mode
    assert run.status == "success"
    assert run.stage == "completed"
    assert run.finished_at is not None
    assert run.applied_settings == result["applied_settings"]
    assert run.applied_settings["max_context_chars"] == 35
    assert run.applied_settings["generation_parameters"] == {
        "model": "control",
        "stream": False,
    }
    assert "temperature" not in run.applied_settings["comparison_parameters"]
    assert "split_by" not in run.applied_settings["comparison_parameters"]
    assert "extract_tables" in run.applied_settings["unsupported_parameters"]
    actual = rag.completion.call_args.kwargs
    digest = hashlib.sha256(
        json.dumps(actual, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert run.details["request_sha256"] == digest
    assert run.details["context_chars"] == result["context_chars"] == 35
    assert run.details["generation_attempted"] is True
    assert "messages" not in run.details  # journal stores no extra copy of content
    if mode == "generate":
        assert "top_k" not in run.applied_settings
        rag.embedder.embed.assert_not_called()


@pytest.mark.parametrize("failure", ["retrieval", "rag_generation", "generate"])
def test_failed_attempt_is_retained_with_settings_and_no_success(
    rag, monkeypatch, failure
):
    assert upload(rag).json()["status"] == "completed"
    if failure == "retrieval":
        monkeypatch.setattr(
            rag.store,
            "search",
            AsyncMock(side_effect=ConnectionError("private detail")),
        )
    else:
        rag.completion.side_effect = RuntimeError("private detail")
    url = f"{rag.chat_url}/generate" if failure == "generate" else rag.chat_url
    payload = (
        {"query": "test"}
        if failure == "generate"
        else {"question": QUESTION, "top_k": 3}
    )
    response = rag.client.post(url, json=payload)
    assert response.status_code == 502
    detail = response.json()["detail"]
    run = get_run(detail["run_id"])
    assert run.status == "error"
    assert run.applied_settings == detail["applied_settings"]
    assert run.error_code == (
        "retrieval_error" if failure == "retrieval" else "generation_error"
    )
    assert run.details["generation_attempted"] == (failure != "retrieval")
    assert "private detail" not in str(detail)
    assert "private detail" not in str(run.details)
    with TestingSessionLocal() as db:
        assert (
            db.query(ExecutionRun).filter(ExecutionRun.status == "success").count() == 0
        )
    # Later edits cannot rewrite the recorded attempt.
    assert (
        rag.client.patch(
            rag.chat_url.removesuffix("/chat"), json={"top_k": 9}
        ).status_code
        == 200
    )
    if failure != "generate":
        assert get_run(detail["run_id"]).applied_settings["top_k"] == 3


def test_empty_prompt_and_environment_fallback_are_recorded_as_actually_used(
    rag, monkeypatch
):
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://example.test/v1")
    project_url = rag.chat_url.removesuffix("/chat")
    assert rag.client.patch(project_url, json={"system_prompt": ""}).status_code == 200
    response = rag.client.post(f"{rag.chat_url}/generate", json={"query": "test"})
    assert response.status_code == 200
    applied = response.json()["applied_settings"]
    assert applied["llm_api_url"] == "https://example.test/v1"
    assert applied["system_prompt"] == OpenAILLM.DEFAULT_SYSTEM_PROMPT
    assert (
        rag.completion.call_args.kwargs["messages"][0]["content"]
        == applied["system_prompt"]
    )


@pytest.mark.parametrize("parameter", ["temperature", "seed", "max_tokens", "top_k"])
def test_generate_rejects_ineffective_parameters_before_starting(rag, parameter):
    response = rag.client.post(
        f"{rag.chat_url}/generate", json={"query": "test", parameter: 1}
    )
    assert response.status_code == 422
    rag.completion.assert_not_called()
    with TestingSessionLocal() as db:
        assert db.query(ExecutionRun).count() == 0


def test_run_settings_exclude_api_credentials(rag):
    project_url = rag.chat_url.removesuffix("/chat")
    assert (
        rag.client.patch(
            project_url,
            json={
                "llm_api_url": "https://user:secret@example.test/v1?token=private#hidden",
                "llm_api_key": "private-api-key",
            },
        ).status_code
        == 200
    )
    response = rag.client.post(f"{rag.chat_url}/generate", json={"query": "test"})
    assert response.status_code == 200
    run = get_run(response.json()["run_id"])
    serialized = str(run.applied_settings)
    assert "secret" not in serialized
    assert "private" not in serialized
    assert "hidden" not in serialized
    assert run.applied_settings["llm_api_url"] == "https://example.test/v1"


def test_client_initialization_failure_is_also_recorded(rag, monkeypatch):
    from unittest.mock import MagicMock

    assert (
        rag.client.patch(
            rag.chat_url.removesuffix("/chat"),
            json={"llm_api_url": "http://[broken"},
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        "backend.core.llm.openai_llm.AsyncOpenAI",
        MagicMock(side_effect=ValueError("Invalid client URL")),
    )
    response = rag.client.post(f"{rag.chat_url}/generate", json={"query": "test"})
    assert response.status_code == 502
    run = get_run(response.json()["detail"]["run_id"])
    assert run.applied_settings["llm_api_url"] == "invalid_url"
    assert run.status == "error"
    assert run.details["generation_attempted"] is False
