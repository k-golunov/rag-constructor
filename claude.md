## Основные правила
- **Python 3.11**, **FastAPI**, **абсолютные импорты** (`from backend.core.models import Document`).
- **Типизация обязательна**, docstring – Google style.
- **Паттерн «Адаптер»**: все модули (парсер, эмбеддер, векторная БД, LLM) наследуются от абстрактных классов в `backend/core/*/base.py`.
- **Единая модель данных**: `Document` из `backend/core/models.py` (Pydantic).
- **Обработка ошибок**: свои исключения, в API – HTTP-статусы.
- **Не менять сигнатуры абстрактных классов** без явного указания.
- **Тесты обязательны** для новой функциональности (pytest).

## Ключевые пути (для быстрой ориентации)
- Абстракции: `backend/core/*/base.py`
- Реализации: `backend/core/*/providers.py` или `parsers.py`/`splitters.py`/`qdrant_store.py`
- API роутеры: `backend/api/*.py`
- Задачи Celery: `backend/tasks/*.py`
- Модели БД: `backend/db/models.py`
- Схемы API: `backend/db/schemas.py`

## Стиль кода
- Смотри примеры в уже существующих файлах (например, `backend/core/ingestion/base.py`).
- Используй `black` (line length 100) и `flake8`.

## Контекст проекта (кратко)
Low-code платформа для RAG-ассистентов по документам. Пользователь загружает PDF → парсинг → чанкинг → эмбеддинги → Qdrant → чат с LLM. Всё конфигурируется через UI без кода.