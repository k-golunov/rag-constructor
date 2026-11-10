# Backend RAG Constructor

FastAPI API версии `0.1.0`. Запускайте из **корня репозитория**, поскольку используются абсолютные импорты `backend.*`:

```bash
./scripts/bootstrap.sh      # первый раз: окружение ./.venv + git-хуки
poetry run uvicorn backend.main:app --reload
```

Единственный источник зависимостей — `pyproject.toml` и `poetry.lock` в корне репозитория;
`requirements.txt` больше нет. Настройка окружения, Poetry, pre-commit и линтеров:
[docs/development.md](../docs/development.md). Инфраструктура и `.env`:
[docs/operations.md](../docs/operations.md).

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
