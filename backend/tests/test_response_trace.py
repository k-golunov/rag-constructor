"""Recover the actual response chain, including failures, usage and retries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from backend.db.models import ConfigurationVersion, ExecutionRun
from backend.tests.conftest import TestingSessionLocal
from backend.tests import test_rag_pipeline as pipeline

rag = pipeline.rag
upload = pipeline.upload


def saved(run_id):
    with TestingSessionLocal() as db:
        return db.get(ExecutionRun, UUID(run_id))


def ask(rag, **extra):
    return rag.client.post(rag.chat_url, json={"question": pipeline.QUESTION, **extra})


def test_rag_trace_reconstructs_question_search_context_messages_answer_and_history(
    rag,
):
    assert upload(rag).json()["status"] == "completed"
    first = ask(rag).json()
    response = ask(rag, session_id=first["session_id"])
    assert response.status_code == 200
    run = saved(response.json()["run_id"])
    trace = run.response_trace
    assert trace["question"] == pipeline.QUESTION
    assert trace["history"] == [
        {"role": "user", "content": pipeline.QUESTION},
        {"role": "assistant", "content": pipeline.ANSWER},
    ]
    assert trace["messages"] == rag.completion.call_args.kwargs["messages"]
    assert trace["system_prompt"] == run.applied_settings["system_prompt"]
    assert trace["context"] in trace["messages"][0]["content"]
    assert trace["answer"] == response.json()["answer"]
    hit = trace["retrieved_chunks"][0]
    manifest = run.applied_settings["documents"][0]
    assert hit["rank"] == 1
    assert hit["score"] > 0.99
    assert hit["text"] == trace["context"]
    assert hit["source"] == manifest["file_name"]
    assert hit["document_version_id"] == manifest["document_version_id"]
    assert hit["document_sha256"] == manifest["sha256"]
    assert trace["context_chunk_ids"] == [hit["id"]]
    assert trace["redaction_applied"] is False
    for stage in (
        "index_resolution",
        "index_preparation",
        "query_embedding",
        "search",
        "context_build",
        "generation",
        "total",
    ):
        assert trace["timings_ms"][stage] >= 0


def test_search_order_and_scores_survive_context_truncation(rag, monkeypatch):
    from backend.core.Document import Document

    assert upload(rag).json()["status"] == "completed"
    docs = [
        Document(
            id="first",
            text="first fragment with answer",
            score=0.9,
            metadata={"source": "one.txt"},
        ),
        Document(
            id="second",
            text="second fragment",
            score=0.4,
            metadata={"source": "two.txt"},
        ),
    ]
    monkeypatch.setattr(rag.store, "search", AsyncMock(return_value=docs))
    result = ask(rag, max_context_chars=5).json()
    trace = saved(result["run_id"]).response_trace
    assert [hit["id"] for hit in trace["retrieved_chunks"]] == ["first", "second"]
    assert [hit["rank"] for hit in trace["retrieved_chunks"]] == [1, 2]
    assert [hit["score"] for hit in trace["retrieved_chunks"]] == [0.9, 0.4]
    assert trace["retrieved_chunks"][1]["text"] == "second fragment"
    assert trace["context"] == "first"
    assert trace["context_chunk_ids"] == ["first"]


@pytest.mark.parametrize(
    "usage, expected",
    [
        (None, None),
        (
            SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
            {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        ),
        (
            SimpleNamespace(total_tokens=14),
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 14},
        ),
        (
            SimpleNamespace(
                total_tokens=14, prompt_tokens_details=SimpleNamespace(cached_tokens=5)
            ),
            {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": 14,
                "prompt_tokens_details": {"cached_tokens": 5, "audio_tokens": None},
            },
        ),
        (
            SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        ),
    ],
)
def test_generation_only_trace_preserves_known_unknown_and_zero_usage(
    rag, usage, expected
):
    rag.completion.return_value.usage = usage
    history = [{"role": "user", "content": "previous question"}]
    response = rag.client.post(
        f"{rag.chat_url}/generate",
        json={
            "query": "current question",
            "context": "c" * 30,
            "max_context_chars": 12,
            "history": history,
        },
    )
    assert response.status_code == 200
    trace = saved(response.json()["run_id"]).response_trace
    assert trace["question"] == "current question"
    assert trace["history"] == history
    assert trace["retrieval_status"] == "not_applicable"
    assert trace["retrieved_chunks"] is None
    assert trace["context"] == "c" * 12
    assert trace["messages"] == rag.completion.call_args.kwargs["messages"]
    assert trace["answer"] == pipeline.ANSWER
    assert trace["token_usage"]["generation"] == expected
    assert trace["attempts"][0]["token_usage"] == expected


def test_generation_error_retains_successful_search_context_and_prepared_request(rag):
    assert upload(rag).json()["status"] == "completed"
    rag.completion.side_effect = RuntimeError("provider rejected the request")
    response = ask(rag, max_attempts=3)
    assert response.status_code == 502
    run = saved(response.json()["detail"]["run_id"])
    trace = run.response_trace
    assert run.status == "error"
    assert trace["retrieval_status"] == "success"
    assert trace["retrieved_chunks"]
    assert trace["context"]
    assert trace["messages"]
    assert trace["answer"] is None
    assert trace["errors"][-1]["stage"] == "generation"
    assert trace["errors"][-1]["retryable"] is False
    assert len([a for a in trace["attempts"] if a["stage"] == "generation"]) == 1


def test_transient_search_failure_is_retried_and_each_attempt_is_saved(
    rag, monkeypatch
):
    assert upload(rag).json()["status"] == "completed"
    docs = asyncio.run(rag.store.search(rag.collection, [1.0, 0.0, 0.0]))
    monkeypatch.setattr(
        rag.store,
        "search",
        AsyncMock(side_effect=[ConnectionError("temporary failure"), docs]),
    )
    response = ask(rag, max_attempts=2)
    assert response.status_code == 200
    trace = saved(response.json()["run_id"]).response_trace
    attempts = [entry for entry in trace["attempts"] if entry["stage"] == "search"]
    assert [entry["number"] for entry in attempts] == [1, 2]
    assert [entry["status"] for entry in attempts] == ["error", "success"]
    assert attempts[0]["will_retry"] is True
    assert trace["retry_count"] == 1
    assert trace["errors"][0]["stage"] == "search"
    rag.completion.assert_awaited_once()


def test_exhausted_search_retries_do_not_generate_or_erase_failure_details(
    rag, monkeypatch
):
    assert upload(rag).json()["status"] == "completed"
    monkeypatch.setattr(
        rag.store, "search", AsyncMock(side_effect=ConnectionError("offline"))
    )
    response = ask(rag, max_attempts=2)
    assert response.status_code == 502
    trace = saved(response.json()["detail"]["run_id"]).response_trace
    assert trace["retrieval_status"] == "error"
    assert trace["retrieved_chunks"] is None
    assert trace["context"] is None
    assert trace["answer"] is None
    assert len(trace["errors"]) == 2
    assert trace["retry_count"] == 1
    rag.completion.assert_not_called()


def test_generation_retry_keeps_unknown_failed_attempt_usage_unknown(rag):
    response_value = rag.completion.return_value
    response_value.usage = SimpleNamespace(
        prompt_tokens=4, completion_tokens=2, total_tokens=6
    )
    rag.completion.side_effect = [ConnectionError("temporary"), response_value]
    response = rag.client.post(
        f"{rag.chat_url}/generate", json={"query": "q", "max_attempts": 2}
    )
    assert response.status_code == 200
    trace = saved(response.json()["run_id"]).response_trace
    assert [attempt["status"] for attempt in trace["attempts"]] == ["error", "success"]
    assert trace["attempts"][0]["token_usage"] is None
    assert trace["attempts"][1]["token_usage"]["total_tokens"] == 6
    assert trace["retry_count"] == 1
    assert trace["token_usage_scope"] == "last_attempt_per_stage"


def test_configured_keys_are_redacted_from_trace_settings_and_error_text(rag):
    secret = "private-secret-api-value"
    assert (
        rag.client.patch(
            rag.chat_url.removesuffix("/chat"),
            json={
                "llm_api_key": secret,
                "system_prompt": f"prompt {secret}",
            },
        ).status_code
        == 200
    )
    rag.completion.side_effect = RuntimeError(
        f"Bearer {secret} https://user:password@example.test/v1?api_key={secret}"
    )
    response = rag.client.post(
        f"{rag.chat_url}/generate",
        json={
            "query": f"question {secret}",
            "context": f"context {secret}",
            "history": [{"role": "assistant", "content": secret}],
        },
    )
    assert response.status_code == 502
    run = saved(response.json()["detail"]["run_id"])
    with TestingSessionLocal() as db:
        config = db.get(ConfigurationVersion, run.configuration_version_id)
        serialized = json.dumps(
            [
                run.response_trace,
                run.details,
                run.applied_settings,
                config.settings,
                response.json(),
            ]
        )
    assert secret not in serialized
    assert "user:password" not in serialized
    assert run.response_trace["redaction_applied"] is True
    assert "[REDACTED]" in serialized


def test_successful_answer_and_legacy_chat_history_do_not_persist_credentials(rag):
    from backend.db.models import ChatHistory

    assert upload(rag).json()["status"] == "completed"
    secret = "private-secret-api-value"
    assert (
        rag.client.patch(
            rag.chat_url.removesuffix("/chat"), json={"llm_api_key": secret}
        ).status_code
        == 200
    )
    rag.completion.return_value.choices[0].message.content = f"answer {secret}"
    response = ask(rag, question=f"question {secret}")
    assert response.status_code == 200
    run = saved(response.json()["run_id"])
    assert secret not in json.dumps(run.response_trace)
    with TestingSessionLocal() as db:
        assert secret not in str(
            [(m.content, m.rag_metadata) for m in db.query(ChatHistory).all()]
        )


def test_embedding_usage_is_preserved_without_estimating_missing_counts():
    from backend.core.embeddings.openai_embedder import OpenAIEmbedder

    embedder = OpenAIEmbedder(api_key="unused")
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])],
        usage=SimpleNamespace(total_tokens=7),
    )
    embedder._async_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    assert asyncio.run(embedder.embed(["text"])) == [[1.0, 0.0]]
    assert embedder.last_usage == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": 7,
    }
    response.usage = None
    asyncio.run(embedder.embed(["text"]))
    assert embedder.last_usage is None


def test_explicit_credential_fields_in_error_text_are_redacted_without_known_value():
    from backend.core.response_trace import redact

    text = (
        '{"api_key": "unknown-provider-secret"} https://host.test/v1?key=another-secret'
    )
    result = redact(text)
    assert "unknown-provider-secret" not in result
    assert "another-secret" not in result
    secret = "private-secret-in-field-name"
    assert secret not in json.dumps(redact({secret: "metadata"}, [secret]))


def test_client_retries_are_disabled_so_attempt_accounting_is_not_hidden(monkeypatch):
    from unittest.mock import MagicMock
    from backend.core.llm.openai_llm import OpenAILLM
    from backend.core.embeddings.openai_embedder import OpenAIEmbedder

    llm_client, embedding_client = MagicMock(), MagicMock()
    monkeypatch.setattr("backend.core.llm.openai_llm.AsyncOpenAI", llm_client)
    monkeypatch.setattr(
        "backend.core.embeddings.openai_embedder.AsyncOpenAI", embedding_client
    )
    OpenAILLM(model="control", api_key="key")
    embedder = OpenAIEmbedder(api_key="key")
    _ = embedder.async_client
    assert llm_client.call_args.kwargs["max_retries"] == 0
    assert embedding_client.call_args.kwargs["max_retries"] == 0
