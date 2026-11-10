# Окружение разработчика

Документ описывает воспроизводимое окружение backend: Poetry, виртуальное окружение внутри
репозитория, pre-commit и линтеры. Все команды выполняются из корня репозитория.

## 1. Что чем управляется

| Файл | Роль |
| --- | --- |
| `pyproject.toml` | Единственный источник зависимостей, настроек ruff, black и pytest |
| `poetry.lock` | Точные версии 110 пакетов с хешами; коммитится в git |
| `poetry.toml` | `virtualenvs.in-project = true` — окружение создаётся в `./.venv` |
| `.python-version` | Требуемая версия интерпретатора (3.12) |
| `.pre-commit-config.yaml` | Хуки, которые запускаются до коммита и до push |
| `Makefile` | Короткие команды: `make setup`, `make lint`, `make test` |
| `scripts/bootstrap.sh` | Одна команда для полной настройки окружения |
| `backend/Dockerfile` | Многостадийная сборка: зависимости экспортируются из `poetry.lock` |

`requirements.txt` удалён: раньше зависимости дублировались в `backend/requirements.txt` и
`backend/pyproject.toml` с разными версиями, и окружение отличалось от манифеста.

## 2. Виртуальное окружение внутри репозитория

По умолчанию Poetry держит окружения в `~/Library/Caches/pypoetry/virtualenvs`. Для проекта
это неудобно: путь не виден, окружение не привязано к каталогу, у разных людей разные
интерпретаторы. Поэтому в `poetry.toml` включён режим `in-project`:

```toml
[virtualenvs]
in-project = true
create = true
```

Теперь окружение лежит в `./.venv`, то есть внутри рабочей копии репозитория:

```bash
poetry env info --path   # /path/to/rag-constructor/.venv
poetry env info          # версия Python, зависимости
poetry env list          # какие окружения привязаны к проекту
```

Сам каталог `.venv` в git не коммитится (`.gitignore`): бинарники и пути внутри него
привязаны к ОС и абсолютным путям, в репозитории они бесполезны. Воспроизводимость даёт пара
`pyproject.toml` + `poetry.lock`: на любой машине и в CI из неё создаётся ровно такое же
окружение.

Воспроизведение с нуля:

```bash
./scripts/bootstrap.sh   # poetry env use 3.12, poetry install, pre-commit install
# или вручную
poetry env use 3.12
poetry install
```

`poetry install` создаёт `./.venv`, ставит 110 пакетов из lock-файла и подключает сам проект
в editable-режиме. Проверка идентичности окружения: `poetry check --lock`.

## 3. Зависимости

Основные зависимости — в `[project].dependencies`, инструменты разработки — в группе `dev`
(`[tool.poetry.group.dev.dependencies]`). В продакшен-образ dev-группа не попадает:

```bash
poetry install                 # окружение разработчика, dev-зависимости включены
poetry install --only main     # только runtime
poetry add <package>           # добавить зависимость и обновить lock
poetry remove <package>
poetry lock --no-update        # пересобрать lock без обновления версий
poetry show --tree             # дерево зависимостей
```

## 4. Линтеры и форматирование

| Инструмент | Что проверяет | Конфигурация |
| --- | --- | --- |
| ruff | pycodestyle, pyflakes, isort, pyupgrade, bugbear, simplify | `[tool.ruff]` в `pyproject.toml` |
| black | форматирование | `[tool.black]` в `pyproject.toml` |
| pytest | тесты | `[tool.pytest.ini_options]` в `pyproject.toml` |

ruff заменил flake8 и isort: один быстрый инструмент вместо двух, правила настраиваются в
`pyproject.toml`, а не в отдельном `.flake8`. Миграции `backend/alembic/versions` исключены —
их генерирует alembic. Кириллица в строках не считается опечаткой (`RUF001`–`RUF003` в
`ignore`), для FastAPI-маркеров `Depends`, `Query`, `File`, `Form` отключено ложное
срабатывание `B008`.

Команды:

```bash
poetry run ruff check backend         # только проверка
poetry run ruff check --fix backend   # автоисправления
poetry run black backend              # форматирование
poetry run black --check backend      # проверка без изменений
make lint                             # то же через make
```

## 5. pre-commit

Хуки запускаются автоматически перед коммитом, поэтому неотформатированный код и «мусорные»
файлы не попадают в историю.

```bash
poetry run pre-commit install --install-hooks   # установить хуки (делает bootstrap.sh)
poetry run pre-commit run --all-files           # прогнать по всему репозиторию
poetry run pre-commit autoupdate                # обновить версии хуков
```

Состав:

- гигиена файлов: `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending`,
  `check-yaml`, `check-toml`, `check-json`, `check-merge-conflict`,
  `check-added-large-files` (лимит 1 МБ), `detect-private-key`, `check-case-conflict`;
- `black` — форматирование Python-кода backend;
- `ruff --fix` — линтер с автоисправлениями; правки возвращаются в индекс и коммит
  прерывается, чтобы изменения попали в diff;
- `poetry check --lock` — манифест и lock-файл не разъехались;
- `pytest` — на стадии `pre-push`, потому что полный прогон слишком долгий для каждого
  коммита.

Обойти хуки можно через `git commit --no-verify`, но CI всё равно запустит
`pre-commit run --all-files`.

## 6. CI и Docker

GitHub Actions (`.github/workflows/ci.yml`) на Python 3.12:

1. `poetry check --lock`;
2. `poetry install`;
3. `pre-commit run --all-files --show-diff-on-failure` — те же хуки, что локально;
4. `pytest --cov=backend`.

`backend/Dockerfile` собирается в два этапа: в builder Poetry экспортирует runtime-зависимости
из `poetry.lock`, в финальный образ ставится только этот список. Poetry и dev-инструменты в
продакшен-образ не попадают, приложение работает под непривилегированным пользователем `app`.

## 7. Типовые проблемы

| Симптом | Причина и решение |
| --- | --- |
| `poetry: command not found` | `pipx install poetry` или `uv tool install poetry` |
| Poetry не находит Python 3.12 | `uv python install 3.12` либо `poetry env use /path/to/python3.12` |
| `pyproject.toml changed significantly` | изменились зависимости: `poetry lock` и закоммитить `poetry.lock` |
| pre-commit ругается на `python3.12` | `pre-commit clean && pre-commit install --install-hooks` |
| Тесты проходят локально, падают в CI | локальное окружение собрано не из lock-файла: `poetry install --sync` |
