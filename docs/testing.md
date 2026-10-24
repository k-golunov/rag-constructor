# Проверки

## Backend

Из корня репозитория:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
```

В `backend/pytest.ini` указан `testpaths = backend/tests`, а импорты абсолютные `backend.*`. Поэтому запуск из корня с явным каталогом надёжнее прежней инструкции запускать только из backend.

Фикстура подменяет `get_db` на SQLite in-memory, но `TestClient` запускает lifespan с настоящим `init_db`. Обычный прогон может подключиться к настроенному PostgreSQL, создать БД и применить миграции. Используйте отдельную тестовую БД или подмену инициализации.

Четыре теста `test_embeddings.py` обращаются к внешнему API, если в окружении процесса есть `OPENAI_API_KEY`. Для исключения внешних вызовов используйте `--ignore=backend/tests/test_embeddings.py`. `.env.test` фикстура автоматически не загружает.

Изолированная команда, использованная при анализе (PowerShell, корень):

```powershell
backend/.venv/Scripts/python.exe -c 'import pytest; from unittest.mock import patch; p = patch("backend.main.init_db"); p.start(); raise SystemExit(pytest.main(["backend/tests", "--ignore=backend/tests/test_embeddings.py", "-p", "no:asyncio", "-q"]))'
```

`init_db` здесь заменён пустой функцией. `-p no:asyncio` обходит ошибку сбора при установленных pytest 9.0.3 / pytest-asyncio 0.23.0. Это обход для текущего синхронного набора, не решение для асинхронных тестов. В `requirements.txt` указан pytest-asyncio 1.4.0, то есть окружение отличается от manifest; чистую установку необходимо проверить отдельно.

`test_parser_splitter.py` — ручная утилита с `main`, а не набор pytest:

```powershell
backend/.venv/Scripts/python.exe -m backend.tests.test_parser_splitter 'C:/documents/guide.pdf'
```

Она печатает фрагменты документа. Тестов загрузки, ZIP, удаления векторов и полного retrieval-чата нет. Тесты чата проверяют `/chat/generate` с подменённым LLM.

## Frontend

Из `frontend`:

```powershell
npm.cmd test -- --run
npm.cmd run build
```

Два теста проверяют локальный упрощённый класс `App`, а не настоящий компонент. E2E нет. В `vitest.config.ts` только комментарий, в `.js` — другой набор настроек, включая typecheck и coverage Istanbul, тогда как в зависимостях есть coverage-v8. Для отчётов покрытия сначала согласуйте конфигурацию и провайдер. Не считайте `.js` активным без явного выбора.

Production build встраивает Google Fonts и требует сетевого доступа. При анализе первый запуск упал на DNS, повторный с доступом к сети прошёл.

## CI и ручной сценарий

GitHub Actions запускает Black, Flake8 и pytest на Python 3.14; frontend проверяется на Node 20.x/22.x. Успешный push в `main` затем собирает и публикует backend-образ в Docker Hub. Полный Compose не развёртывается. Jenkins выполняет установку, тесты backend/frontend и сборку frontend.

Ручная сквозная проверка: создать проект → загрузить TXT → убедиться в `completed` → задать вопрос с известным ответом → проверить источники и SQL-историю. Отдельно проверить ZIP, отказ Qdrant, смену модели и удаление. В данном анализе внешние эмбеддинги/LLM, реальная БД и контейнерный сквозной сценарий не запускались.
