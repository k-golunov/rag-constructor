# Запуск и эксплуатация

Основной сценарий — в [README](../README.md). Команды рассчитаны на PowerShell и корень репозитория.

## Настройки

Скопируйте `.env.example` в `.env`. Приложение выбирает первый существующий файл: `backend/.env`, корневой `.env`, `.env` текущего каталога. Файлы между собой не объединяются. Переменные процесса имеют приоритет над dotenv.

| Переменная | Значение по умолчанию в `config.py` / назначение |
| --- | --- |
| `DATABASE_URL` | Собирается из `POSTGRES_*`, если не задан; явный URL имеет приоритет |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `localhost`, `5432` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `postgres`, `1`, `rag_constructor`; в `.env.example` пароль `postgres` |
| `QDRANT_URL`, `QDRANT_API_KEY` | `http://localhost:6333`, необязательный ключ |
| `REDIS_URL` | `redis://localhost:6379/0`, брокер и результаты Celery |
| `OPENAI_API_KEY` | Общий запасной ключ в builder-функциях |
| `OPENAI_BASE_URL` | Запасной URL эмбеддингов; builder LLM явно его не передаёт |
| `UPLOAD_DIR` | `./uploads`, относительно рабочего каталога |

У проекта свои ключи и URL для эмбеддингов и LLM. Для одинакового поведения двух путей генерации указывайте `llm_api_key` и при необходимости `llm_api_url` в проекте: `/chat/generate` требует ключ именно проекта. `OPENAI_BASE_URL` также может читаться самим SDK; для LLM предпочтительнее явное поле проекта.

В Compose блок `x-backend-env` фиксирует SQL-реквизиты и адреса инфраструктуры. Корневой `.env` используется для подстановок и целиком не передаётся в контейнер: сейчас подставляются `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `BACKEND_PORT`. `QDRANT_API_KEY` в environment Compose не включён. `BACKEND_PORT` задавайте переменной терминала: в Pydantic Settings такого поля нет, неизвестная запись dotenv может вызвать ошибку валидации.

## Инфраструктура и данные

```powershell
docker compose up -d postgres qdrant redis
docker compose ps
docker compose logs --tail 100 postgres qdrant redis
```

Порты 5432, 6333 и 6379 должны быть свободны. Именованные volumes: `pgdata`, `qdrant_storage`, `uploads`. Локальный backend пишет в каталог `./uploads`, контейнерный — в volume `/app/uploads`: это разные хранилища. Корневой `.gitignore` не исключает `uploads/`; перед коммитом проверяйте список добавляемых файлов.

`docker compose stop` сохраняет данные. Для резервного копирования нужны PostgreSQL, Qdrant и оригиналы файлов. Скриптов согласованного резервного копирования/восстановления в проекте нет.

## Миграции

Startup подключается к служебной БД `postgres`, проверяет целевую БД, при необходимости создаёт её и вызывает Alembic `upgrade head`. Нужен доступ к служебной БД и право создания целевой БД, если её ещё нет. Ошибка инициализации только логируется: сервер продолжает работу.

Ручные команды с явным корнем импортов:

```powershell
$env:PYTHONPATH = (Get-Location).Path
Push-Location backend
.venv/Scripts/python.exe -m alembic current
.venv/Scripts/python.exe -m alembic history
.venv/Scripts/python.exe -m alembic upgrade head
Pop-Location
```

Новые ревизии создаются там же через `alembic revision --autogenerate -m "описание"`; сгенерированный код нужно проверить до применения. Файлы находятся в `backend/alembic/versions`.

## Диагностика

| Симптом | Проверка |
| --- | --- |
| Health отвечает, CRUD падает | Лог инициализации, доступность PostgreSQL, реквизиты, миграции |
| UI не получает проекты | URL в `environment.ts`, порт backend, origin `http://localhost:4200` |
| Загрузка вернула 200, но источник `failed` | `error_message` в списке источников, ключ/URL эмбеддингов, Qdrant, размерность |
| PDF возвращает 422 | Наличие текстового слоя; OCR отсутствует |
| Ответ без источников | Лог retrieval: ошибка поиска не останавливает чат |
| ZIP status возвращает 404 | Перезапуск API, другой процесс, неверный ID |
| Удалённый источник остаётся в ответах | Его векторы пока не удаляются |
| Ошибка splitter | `chunk_overlap` должен быть меньше `chunk_size` |
| Сборка не загружает шрифты | Доступ к Google Fonts: production build встраивает внешние шрифты |

## Ограничения полного Compose

Полный сценарий предусмотрен командой `docker compose up --build`, но в анализе не запускался. Frontend Dockerfile использует `npm run build --prod`; требуется согласовать команду с явной Angular-конфигурацией и проверить образ. В `angular.json` нет переключения на `environment.prod.ts`, поэтому браузер использует `127.0.0.1:8000`, обходя nginx `/api`. На другом компьютере этот адрес указывает на самого клиента. Изменение `BACKEND_PORT` также не меняет собранный URL.

Worker не ожидает миграций backend, а модуль задач явно не подключён через include/autodiscover; перед использованием Celery нужна проверка регистрации. Текущие HTTP-загрузки worker не используют.

Это окружение разработки: API без авторизации, ключи проектов хранятся и возвращаются открытым текстом, инфраструктурные порты опубликованы, backend работает с `--reload`. Перед внешним размещением эти ограничения нужно устранить.
