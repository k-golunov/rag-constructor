"""
Тесты для эндпоинта POST /api/v1/public/provider/projects/{project_id}/chat/generate.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


BASE_URL = "/api/v1/public/provider/projects"


def _project_payload(**overrides) -> dict:
    base = {
        "name": "Test Project",
        "embedding_model": "text-embedding-3-small",
        "llm_model": "gpt-4o-mini",
        "llm_api_key": "sk-test-key",
    }
    base.update(overrides)
    return base


def _mock_openai_response(text: str) -> MagicMock:
    """Создаёт mock-ответ, совместимый с openai.ChatCompletion."""
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# Успешная генерация
# ---------------------------------------------------------------------------


class TestGenerateAnswer:
    def test_generate_returns_answer(self, client):
        project = client.post(f"{BASE_URL}/", json=_project_payload()).json()
        project_id = project["id"]

        mock_response = _mock_openai_response("Это тестовый ответ.")

        with patch("backend.core.llm.openai_llm.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_cls.return_value = mock_client

            response = client.post(
                f"{BASE_URL}/{project_id}/chat/generate",
                json={"query": "Что такое RAG?", "context": "RAG — это..."},
            )

        assert response.status_code == 200
        assert response.json()["answer"] == "Это тестовый ответ."

    def test_generate_without_context(self, client):
        project = client.post(f"{BASE_URL}/", json=_project_payload()).json()
        project_id = project["id"]

        mock_response = _mock_openai_response("Ответ без контекста.")

        with patch("backend.core.llm.openai_llm.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_cls.return_value = mock_client

            response = client.post(
                f"{BASE_URL}/{project_id}/chat/generate",
                json={"query": "Привет"},
            )

        assert response.status_code == 200
        assert response.json()["answer"] == "Ответ без контекста."

    def test_generate_with_history(self, client):
        project = client.post(f"{BASE_URL}/", json=_project_payload()).json()
        project_id = project["id"]

        history = [
            {"role": "user", "content": "Предыдущий вопрос"},
            {"role": "assistant", "content": "Предыдущий ответ"},
        ]
        mock_response = _mock_openai_response("Ответ с историей.")
        captured_messages = []

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return mock_response

        with patch("backend.core.llm.openai_llm.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = capture_create
            mock_openai_cls.return_value = mock_client

            response = client.post(
                f"{BASE_URL}/{project_id}/chat/generate",
                json={"query": "Новый вопрос", "context": "", "history": history},
            )

        assert response.status_code == 200
        roles = [m["role"] for m in captured_messages]
        assert roles == ["system", "user", "assistant", "user"]

    def test_context_truncated_to_2000_chars(self, client):
        project = client.post(f"{BASE_URL}/", json=_project_payload()).json()
        project_id = project["id"]

        long_context = "x" * 5000
        mock_response = _mock_openai_response("ok")
        captured_messages = []

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return mock_response

        with patch("backend.core.llm.openai_llm.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = capture_create
            mock_openai_cls.return_value = mock_client

            response = client.post(
                f"{BASE_URL}/{project_id}/chat/generate",
                json={"query": "Вопрос", "context": long_context},
            )

        assert response.status_code == 200
        system_content = captured_messages[0]["content"]
        # Контекст должен быть обрезан: 2000 символов 'x' + заголовок "...\nКонтекст:\n"
        assert "x" * 2001 not in system_content
        assert "x" * 2000 in system_content


# ---------------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------------


class TestGenerateErrors:
    def test_project_not_found(self, client):
        fake_id = uuid4()
        response = client.post(
            f"{BASE_URL}/{fake_id}/chat/generate",
            json={"query": "Вопрос"},
        )
        assert response.status_code == 404

    def test_llm_not_configured_no_api_key(self, client):
        project = client.post(
            f"{BASE_URL}/",
            json=_project_payload(llm_api_key=None),
        ).json()
        response = client.post(
            f"{BASE_URL}/{project['id']}/chat/generate",
            json={"query": "Вопрос"},
        )
        assert response.status_code == 422
        assert "llm_api_key" in response.json()["detail"]

    def test_empty_query_rejected(self, client):
        project = client.post(f"{BASE_URL}/", json=_project_payload()).json()
        response = client.post(
            f"{BASE_URL}/{project['id']}/chat/generate",
            json={"query": ""},
        )
        assert response.status_code == 422
