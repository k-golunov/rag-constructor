# Backend RAG Constructor

FastAPI API версии `0.1.0`. Запускайте из **корня репозитория**, поскольку используются абсолютные импорты `backend.*`:

```powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --reload
```

Настройка инфраструктуры и `.env`: [руководство](../docs/operations.md). Источник зависимостей для текущих Docker/CI-сценариев — `requirements.txt`. В `pyproject.toml` находится другой список версий и нестандартная таблица `[project.dependencies]`; установка через `pip install -e backend` не является проверенным способом запуска.

| Путь | Назначение |
| --- | --- |
| `main.py`, `config.py` | Приложение, CORS, startup и настройки |
| `api/` | Проекты, загрузки, операции ZIP и чат |
| `db/` | Модели SQLAlchemy, схемы Pydantic, сессии и инициализация |
| `alembic/` | Миграции PostgreSQL |
| `core/ingestion/` | Парсеры и разбиение текста |
| `core/embeddings/`, `core/llm/`, `core/vector_store/` | Адаптеры провайдеров и Qdrant |
| `core/factories.py` | Реестры и создание компонентов |
| `tasks/` | Celery-приложение и отдельная задача обработки документа |
| `tests/` | Тесты проектов, health, генерации и внешних эмбеддингов |

Swagger: `http://127.0.0.1:8000/docs`. Общий префикс API: `/api/v1/public/provider`; `/health` находится вне префикса.

См. [контракты API](../docs/api.md), [архитектуру](../docs/architecture.md) и [проверки](../docs/testing.md). Обычный TestClient запускает реальную инициализацию БД; для изолированной проверки используйте описанную там подмену startup.
