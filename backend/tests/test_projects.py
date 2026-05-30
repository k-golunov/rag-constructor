"""
Тесты REST API для управления проектами.

Покрытие:
    - POST   /projects/       — создание проекта (успех + валидация)
    - GET    /projects/       — список проектов
    - GET    /projects/{id}   — получение по ID (успех + 404)
    - PATCH  /projects/{id}   — частичное обновление (успех + 404)
    - DELETE /projects/{id}   — удаление (успех + 404 повторно)
"""

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Вспомогательные данные
# ---------------------------------------------------------------------------

BASE_URL = "/api/v1/public/provider/projects"

VALID_PROJECT_PAYLOAD = {
    "name": "Тестовый проект",
    "chunk_size": 500,
    "chunk_overlap": 50,
    "embedding_model": "text-embedding-3-small",
    "llm_model": "gpt-4o-mini",
    "system_prompt": "Ты полезный ассистент.",
}


def _create_project(client: TestClient, payload: dict | None = None) -> dict:
    """Вспомогательная функция: создаёт проект и возвращает JSON-ответ."""
    response = client.post(f"{BASE_URL}/", json=payload or VALID_PROJECT_PAYLOAD)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# POST /projects/
# ---------------------------------------------------------------------------


class TestCreateProject:
    def test_create_returns_201(self, client: TestClient):
        """Успешное создание возвращает 201 и корректное тело."""
        response = client.post(f"{BASE_URL}/", json=VALID_PROJECT_PAYLOAD)

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == VALID_PROJECT_PAYLOAD["name"]
        assert data["chunk_size"] == 500
        assert data["chunk_overlap"] == 50
        assert data["embedding_model"] == "text-embedding-3-small"
        assert data["llm_model"] == "gpt-4o-mini"
        assert "id" in data
        assert "created_at" in data

    def test_create_uses_default_chunk_params(self, client: TestClient):
        """При отсутствии chunk_size/overlap подставляются дефолты (800/100)."""
        payload = {
            "name": "Проект с дефолтами",
            "embedding_model": "text-embedding-3-small",
            "llm_model": "gpt-4o-mini",
        }
        response = client.post(f"{BASE_URL}/", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["chunk_size"] == 800
        assert data["chunk_overlap"] == 100

    def test_create_missing_required_field_returns_422(self, client: TestClient):
        """Отсутствие обязательного поля name — ошибка валидации 422."""
        payload = {
            "embedding_model": "text-embedding-3-small",
            "llm_model": "gpt-4o-mini",
        }
        response = client.post(f"{BASE_URL}/", json=payload)

        assert response.status_code == 422

    def test_create_missing_embedding_model_returns_422(self, client: TestClient):
        """Отсутствие embedding_model — ошибка валидации 422."""
        payload = {"name": "Проект", "llm_model": "gpt-4o-mini"}
        response = client.post(f"{BASE_URL}/", json=payload)

        assert response.status_code == 422

    def test_create_chunk_size_too_small_returns_422(self, client: TestClient):
        """chunk_size < 100 не проходит валидацию."""
        payload = {**VALID_PROJECT_PAYLOAD, "chunk_size": 10}
        response = client.post(f"{BASE_URL}/", json=payload)

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /projects/
# ---------------------------------------------------------------------------


class TestListProjects:
    def test_list_empty_db(self, client: TestClient):
        """Пустая БД — возвращает total=0 и пустой список."""
        response = client.get(f"{BASE_URL}/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_returns_created_projects(self, client: TestClient):
        """После создания двух проектов список содержит оба."""
        _create_project(client, {**VALID_PROJECT_PAYLOAD, "name": "Проект 1"})
        _create_project(client, {**VALID_PROJECT_PAYLOAD, "name": "Проект 2"})

        response = client.get(f"{BASE_URL}/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_pagination(self, client: TestClient):
        """Параметры skip и limit работают корректно."""
        for i in range(5):
            _create_project(client, {**VALID_PROJECT_PAYLOAD, "name": f"Проект {i}"})

        response = client.get(f"{BASE_URL}/?skip=2&limit=2")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# GET /projects/{id}
# ---------------------------------------------------------------------------


class TestGetProject:
    def test_get_existing_project(self, client: TestClient):
        """Созданный проект можно получить по его ID."""
        created = _create_project(client)
        project_id = created["id"]

        response = client.get(f"{BASE_URL}/{project_id}")

        assert response.status_code == 200
        assert response.json()["id"] == project_id
        assert response.json()["name"] == VALID_PROJECT_PAYLOAD["name"]

    def test_get_nonexistent_project_returns_404(self, client: TestClient):
        """Несуществующий UUID — 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"{BASE_URL}/{fake_id}")

        assert response.status_code == 404
        assert "не найден" in response.json()["detail"]


# ---------------------------------------------------------------------------
# PATCH /projects/{id}
# ---------------------------------------------------------------------------


class TestUpdateProject:
    def test_update_name(self, client: TestClient):
        """Обновление имени проекта возвращает актуальные данные."""
        created = _create_project(client)
        project_id = created["id"]

        response = client.patch(
            f"{BASE_URL}/{project_id}",
            json={"name": "Новое название"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Новое название"
        # Остальные поля не изменились
        assert data["chunk_size"] == created["chunk_size"]
        assert data["llm_model"] == created["llm_model"]

    def test_update_multiple_fields(self, client: TestClient):
        """Обновление нескольких полей за один запрос."""
        created = _create_project(client)
        project_id = created["id"]

        response = client.patch(
            f"{BASE_URL}/{project_id}",
            json={
                "chunk_size": 1000,
                "chunk_overlap": 200,
                "llm_model": "gpt-4o",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["chunk_size"] == 1000
        assert data["chunk_overlap"] == 200
        assert data["llm_model"] == "gpt-4o"

    def test_update_empty_body_changes_nothing(self, client: TestClient):
        """Пустой PATCH не меняет данные проекта."""
        created = _create_project(client)
        project_id = created["id"]

        response = client.patch(f"{BASE_URL}/{project_id}", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == created["name"]
        assert data["chunk_size"] == created["chunk_size"]

    def test_update_nonexistent_project_returns_404(self, client: TestClient):
        """PATCH несуществующего проекта — 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.patch(f"{BASE_URL}/{fake_id}", json={"name": "X"})

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /projects/{id}
# ---------------------------------------------------------------------------


class TestDeleteProject:
    def test_delete_returns_204(self, client: TestClient):
        """Удаление существующего проекта возвращает 204 No Content."""
        created = _create_project(client)
        project_id = created["id"]

        response = client.delete(f"{BASE_URL}/{project_id}")

        assert response.status_code == 204
        assert response.content == b""  # тело пустое

    def test_deleted_project_not_found(self, client: TestClient):
        """После удаления проект недоступен — 404."""
        created = _create_project(client)
        project_id = created["id"]

        client.delete(f"{BASE_URL}/{project_id}")
        response = client.get(f"{BASE_URL}/{project_id}")

        assert response.status_code == 404

    def test_deleted_project_removed_from_list(self, client: TestClient):
        """После удаления проект исчезает из общего списка."""
        _create_project(client, {**VALID_PROJECT_PAYLOAD, "name": "Оставить"})
        to_delete = _create_project(
            client, {**VALID_PROJECT_PAYLOAD, "name": "Удалить"}
        )

        client.delete(f"{BASE_URL}/{to_delete['id']}")

        response = client.get(f"{BASE_URL}/")
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Оставить"

    def test_delete_nonexistent_project_returns_404(self, client: TestClient):
        """DELETE несуществующего UUID — 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.delete(f"{BASE_URL}/{fake_id}")

        assert response.status_code == 404
